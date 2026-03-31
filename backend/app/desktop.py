from __future__ import annotations

import queue
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Callable

import numpy as np
import sounddevice as sd
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

from .core import (
    MODEL_SIZE,
    find_progressive_match,
    get_runtime_device,
    transcribe_audio_file_with_options,
)
from .document_import import extract_text_from_path

APP_DISPLAY_NAME = "Voice Active Prompter"


def apply_macos_app_menu_name(root: tk.Tk, name: str) -> None:
    """macOS 메뉴 맨 왼쪽(앱 메뉴)에 표시되는 이름. Dock 이름은 여전히 python일 수 있음."""
    if sys.platform != "darwin":
        return
    for call in (
        ("tk::mac::AppMenuName", name),
        ("tk", "mac::AppMenuName", name),
    ):
        try:
            root.tk.call(*call)
            return
        except tk.TclError:
            continue


DEFAULT_SCRIPT = """안녕하세요. 이 텔레프롬프터는 선택한 마이크를 통해 들리는 음성을 인식합니다.
인식된 문장을 기준으로 현재 대본 위치를 계산해서 화면을 자동으로 넘깁니다.
발표자는 마우스 없이도 계속 읽을 수 있고, 진행 상태는 노란색 하이라이트로 표시됩니다.
이제 웹 브라우저가 아니라 프로그램 자체 창에서 바로 실행할 수 있습니다."""
INITIAL_RECOGNIZED_MESSAGE = "아직 인식된 음성이 없습니다."
LISTENING_MESSAGE = "듣는 중입니다... 마이크에 대고 한 문장 읽어보세요."


class LiveMicTranscriber:
    def __init__(
        self,
        device_id: int,
        on_text: Callable[[str], None],
        on_status: Callable[[str], None],
        on_level: Callable[[float], None] | None = None,
        chunk_seconds: float = 1.4,
        sample_rate: int = 16000,
        language: str | None = "ko",
        beam_size: int = 3,
        best_of: int = 3,
        gate_peak_threshold: float = 0.0025,
        gate_rms_threshold: float = 0.0007,
        initial_prompt: str | None = None,
        script_prompt_provider: Callable[[], str] | None = None,
    ) -> None:
        self.device_id = device_id
        self.on_text = on_text
        self.on_status = on_status
        self.on_level = on_level
        self.chunk_seconds = chunk_seconds
        self.sample_rate = sample_rate
        self.language = language
        self.beam_size = beam_size
        self.best_of = best_of
        self.gate_peak_threshold = gate_peak_threshold
        self.gate_rms_threshold = gate_rms_threshold
        self.initial_prompt = initial_prompt
        self.script_prompt_provider = script_prompt_provider
        self.audio_queue: queue.Queue[np.ndarray] = queue.Queue()
        self.stop_event = threading.Event()
        self.stream: sd.InputStream | None = None
        self.worker_thread: threading.Thread | None = None
        self.last_text = ""
        self.recent_transcript = ""

    def start(self) -> None:
        self.stop_event.clear()
        self.stream = sd.InputStream(
            device=self.device_id,
            channels=1,
            samplerate=self.sample_rate,
            dtype="float32",
            latency="low",
            blocksize=max(int(self.sample_rate * 0.12), 1024),
            callback=self._audio_callback,
        )
        self.stream.start()
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        self.on_status("마이크 입력을 듣는 중입니다.")

    def stop(self) -> None:
        self.stop_event.set()
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=1.0)
        self.on_status("대기 중입니다.")

    def _audio_callback(self, indata, frames, time, status) -> None:  # noqa: ARG002
        if status:
            self.on_status(f"오디오 경고: {status}")
        if self.on_level is not None:
            level = float(np.sqrt(np.mean(np.square(indata))) + 1e-8)
            self.on_level(min(level * 100.0, 100.0))
        self.audio_queue.put(indata.copy())

    def _worker_loop(self) -> None:
        buffered = np.empty((0, 1), dtype=np.float32)

        while not self.stop_event.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            buffered = np.concatenate((buffered, chunk), axis=0)
            buffered_seconds = len(buffered) / self.sample_rate
            if buffered_seconds < self.chunk_seconds:
                continue

            # Keep a small rolling window so recognition stays responsive.
            if buffered_seconds > self.chunk_seconds * 2.2:
                buffered = buffered[-int(self.sample_rate * self.chunk_seconds * 2.2) :]

            text = self._transcribe_chunk(buffered)
            buffered = buffered[-int(self.sample_rate * 0.65) :]

            if not text or text == self.last_text:
                continue

            self.last_text = text
            self.on_text(text)

    def _transcribe_chunk(self, audio: np.ndarray) -> str:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_file:
            temp_path = Path(temp_file.name)

        try:
            prepared = self._prepare_audio(audio)
            if prepared is None:
                return ""

            sf.write(temp_path, prepared.squeeze(), self.sample_rate)
            result = transcribe_audio_file_with_options(
                temp_path,
                language=self.language,
                beam_size=self.beam_size,
                best_of=self.best_of,
                vad_filter=True,
                condition_on_previous_text=True,
                initial_prompt=self._compose_prompt(),
            )
            text = str(result["text"]).strip()
            if text:
                self.recent_transcript = self._merge_recent_text(self.recent_transcript, text)
            return text
        except Exception as exc:
            self.on_status(f"음성 인식 오류: {exc}")
            return ""
        finally:
            temp_path.unlink(missing_ok=True)

    def _compose_prompt(self) -> str | None:
        recent = self.recent_transcript[-120:].strip()
        script_hint = ""
        if self.script_prompt_provider is not None:
            script_hint = self.script_prompt_provider().strip()
        parts = [part for part in [self.initial_prompt, script_hint, recent] if part]
        return " ".join(parts) if parts else None

    def _prepare_audio(self, audio: np.ndarray) -> np.ndarray | None:
        mono = audio.squeeze().astype(np.float32)
        if mono.size == 0:
            return None

        mono = mono - float(np.mean(mono))
        peak = float(np.max(np.abs(mono)))
        rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-8)

        # Lower gate threshold so quieter speech is still forwarded to Whisper.
        if peak < self.gate_peak_threshold or rms < self.gate_rms_threshold:
            return None

        target_peak = 0.92
        gain = min(target_peak / max(peak, 1e-6), 8.0)
        normalized = np.clip(mono * gain, -1.0, 1.0)
        return normalized.reshape(-1, 1)

    @staticmethod
    def _merge_recent_text(previous: str, current: str) -> str:
        if not previous:
            return current

        lower_previous = previous.lower()
        lower_current = current.lower()
        max_overlap = min(len(lower_previous), len(lower_current), 40)

        for size in range(max_overlap, 5, -1):
            if lower_previous.endswith(lower_current[:size]):
                return f"{previous}{current[size:]}"

        return f"{previous} {current}".strip()


class SubtitleWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.window = tk.Toplevel(root)
        self.window.title("Live Subtitle Output")
        self.window.geometry("1180x220+160+40")
        self.window.configure(bg="#05070a")
        self.is_visible = False
        self.is_mirrored = False
        self.current_text = "실시간 자막 출력창"
        self.photo_image: ImageTk.PhotoImage | None = None

        self.label = tk.Label(
            self.window,
            text=self.current_text,
            bg="#05070a",
            fg="#f8f2df",
            font=("Helvetica", 28, "bold"),
            wraplength=1080,
            justify="center",
            padx=30,
            pady=30,
        )
        self.label.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            self.window,
            bg="#05070a",
            highlightthickness=0,
            bd=0,
        )
        self.canvas_image = self.canvas.create_image(0, 0, anchor="center")

        self.window.bind("<Configure>", self._on_resize)
        self.window.bind("<Escape>", lambda _event: self.exit_presentation_mode())
        self.hide()

    def update_text(self, text: str) -> None:
        self.current_text = text or " "
        if self.is_mirrored:
            self.label.pack_forget()
            self.canvas.pack(fill="both", expand=True)
            self._render_mirrored_text()
        else:
            self.canvas.pack_forget()
            self.label.pack(fill="both", expand=True)
            self.label.config(text=self.current_text)

    def show(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.attributes("-topmost", True)
        self.is_visible = True

    def hide(self) -> None:
        self.window.withdraw()
        self.is_visible = False

    def set_mirrored(self, mirrored: bool) -> None:
        self.is_mirrored = mirrored
        self.update_text(self.current_text)

    def enter_presentation_mode(self) -> None:
        self.show()
        self.window.attributes("-fullscreen", True)
        self.window.focus_force()

    def exit_presentation_mode(self) -> None:
        self.window.attributes("-fullscreen", False)
        self.hide()

    def _on_resize(self, _event) -> None:
        if self.is_mirrored:
            self._render_mirrored_text()

    def _render_mirrored_text(self) -> None:
        width = max(self.window.winfo_width(), 200)
        height = max(self.window.winfo_height(), 120)
        image = Image.new("RGBA", (width, height), "#05070a")
        draw = ImageDraw.Draw(image)

        font = self._load_presentation_font(52)

        text_box_width = max(width - 120, 200)
        wrapped_lines = self._wrap_text(draw, self.current_text, font, text_box_width)
        display_text = "\n".join(wrapped_lines) if wrapped_lines else " "
        bbox = draw.multiline_textbbox((0, 0), display_text, font=font, spacing=16, align="center")
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (width - text_width) / 2
        y = (height - text_height) / 2
        draw.multiline_text(
            (x, y),
            display_text,
            font=font,
            fill="#f8f2df",
            spacing=16,
            align="center",
        )

        mirrored = ImageOps.mirror(image)
        self.photo_image = ImageTk.PhotoImage(mirrored)
        self.canvas.config(width=width, height=height)
        self.canvas.coords(self.canvas_image, width / 2, height / 2)
        self.canvas.itemconfigure(self.canvas_image, image=self.photo_image)

    @staticmethod
    def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        words = text.split()
        if not words:
            return [text]

        lines: list[str] = []
        current = words[0]

        for word in words[1:]:
            candidate = f"{current} {word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word

        lines.append(current)
        return lines

    @staticmethod
    def _load_presentation_font(size: int) -> ImageFont.ImageFont:
        font_candidates = [
            "/System/Library/Fonts/AppleSDGothicNeo.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        ]

        for font_path in font_candidates:
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue

        return ImageFont.load_default()


class TeleprompterDesktopApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_DISPLAY_NAME)
        apply_macos_app_menu_name(self.root, APP_DISPLAY_NAME)
        self.root.geometry("1480x920")
        self.root.configure(bg="#1e1e1e")
        self.icon_image: ImageTk.PhotoImage | None = None

        self.current_index = 0
        self.transcriber: LiveMicTranscriber | None = None
        self.monitor_stream: sd.InputStream | None = None
        self.level_queue: queue.Queue[float] = queue.Queue()
        self.devices: list[dict[str, object]] = []
        self.status_var = tk.StringVar(value="초기화 중입니다.")
        self.recognized_var = tk.StringVar(value=INITIAL_RECOGNIZED_MESSAGE)
        self.last_action_var = tk.StringVar(value="최근 버튼: 아직 없음")
        self.listen_state_var = tk.StringVar(value="상태: 대기")
        self.device_var = tk.StringVar()
        self.language_var = tk.StringVar(value="ko")
        self.chunk_seconds_var = tk.DoubleVar(value=1.4)
        self.subtitle_enabled_var = tk.BooleanVar(value=False)
        self.subtitle_mirror_var = tk.BooleanVar(value=False)
        self.progress_window_var = tk.IntVar(value=260)
        self.confidence_var = tk.DoubleVar(value=0.22)
        self.input_sensitivity_var = tk.DoubleVar(value=1.6)
        self.input_sensitivity_display_var = tk.StringVar(value="")
        self.recognition_preset_var = tk.StringVar(value="균형")
        self.accuracy_priority_var = tk.BooleanVar(value=False)
        self.speed_priority_var = tk.BooleanVar(value=False)
        self.level_var = tk.DoubleVar(value=0.0)
        self.level_meter_segments = 18
        self.display_level = 0.0
        self.peak_hold_level = 0.0
        self.peak_hold_time = 0.0
        self.is_listening = False
        self.auto_listen_on_device_select = True
        self.subtitle_window = SubtitleWindow(root)

        self._build_ui()
        self.refresh_presentation_mirror_button()
        self.refresh_priority_mode_buttons()
        self.refresh_input_sensitivity_display()
        self.refresh_listening_ui()
        self.apply_app_icon()
        self.apply_script(DEFAULT_SCRIPT)
        self.refresh_devices()
        self.set_status(f"Whisper model={MODEL_SIZE}, device={get_runtime_device()}")
        self.root.after(50, self.process_level_queue)
        self._ensure_main_window_visible()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def apply_app_icon(self) -> None:
        icon_path = Path(__file__).resolve().parent.parent / "assets" / "app_icon.png"
        if not icon_path.exists():
            return

        try:
            image = Image.open(icon_path)
            self.icon_image = ImageTk.PhotoImage(image)
            self.root.iconphoto(True, self.icon_image)
            self.subtitle_window.window.iconphoto(True, self.icon_image)
        except Exception:
            self.icon_image = None

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#1e1e1e")
        style.configure("TLabel", background="#1e1e1e", foreground="#d8d8d8")
        style.configure("Header.TLabel", font=("Helvetica", 18, "bold"), foreground="#f0f0f0")
        style.configure("Sub.TLabel", foreground="#8e8e8e", background="#2b2b2b", font=("Helvetica", 10, "bold"))
        style.configure("PanelTitle.TLabel", foreground="#cfcfcf", background="#2b2b2b", font=("Helvetica", 9, "bold"))
        style.configure("TButton", padding=7, background="#3a3a3a", foreground="#f2f2f2", borderwidth=0)
        style.map(
            "TButton",
            background=[("pressed", "#1e5a8a"), ("active", "#4a4a4a")],
            foreground=[("pressed", "#ffffff"), ("active", "#ffffff")],
        )
        style.configure(
            "Card.TFrame",
            background="#2b2b2b",
            relief="flat",
        )
        style.configure(
            "Level.Horizontal.TProgressbar",
            troughcolor="#181818",
            bordercolor="#181818",
            background="#d97b29",
            lightcolor="#d97b29",
            darkcolor="#d97b29",
        )
        style.configure("Dark.TCheckbutton", background="#2b2b2b", foreground="#d0d0d0")

        outer = tk.Frame(self.root, bg="#1e1e1e")
        outer.pack(fill="both", expand=True, padx=12, pady=12)

        topbar = tk.Frame(outer, bg="#252526", height=40)
        topbar.pack(fill="x", pady=(0, 10))
        topbar.pack_propagate(False)
        tk.Label(
            topbar,
            text="Voice Active Prompter",
            bg="#252526",
            fg="#f1f1f1",
            font=("Helvetica", 13, "bold"),
            padx=12,
        ).pack(side="left")
        tk.Label(
            topbar,
            text="Editing",
            bg="#3a3a3a",
            fg="#d0d0d0",
            font=("Helvetica", 10),
            padx=10,
            pady=5,
        ).pack(side="left", padx=(0, 8))
        tk.Label(
            topbar,
            text="Audio Sync",
            bg="#2d2d30",
            fg="#8e8e8e",
            font=("Helvetica", 10),
            padx=10,
            pady=5,
        ).pack(side="left")

        paned = tk.PanedWindow(
            outer,
            orient="horizontal",
            sashwidth=8,
            sashrelief="flat",
            bg="#1e1e1e",
            bd=0,
            opaqueresize=True,
        )
        paned.pack(fill="both", expand=True)

        left_panel = tk.Frame(paned, bg="#252526", width=460)
        self.left_panel = left_panel
        right_shell = tk.Frame(paned, bg="#1e1e1e")
        paned.add(left_panel, minsize=340, stretch="never")
        paned.add(right_shell, minsize=640, stretch="always")
        self.left_panel.bind("<Configure>", self.on_left_panel_resize)

        left = ttk.Frame(left_panel, style="Card.TFrame", padding=14)
        left.pack(fill="both", expand=True)

        header_strip = tk.Frame(left, bg="#2d2d30", height=34)
        header_strip.pack(fill="x", pady=(0, 10))
        header_strip.pack_propagate(False)
        tk.Label(
            header_strip,
            text="CONTROL PANEL",
            bg="#2d2d30",
            fg="#cfcfcf",
            font=("Helvetica", 10, "bold"),
            padx=10,
        ).pack(side="left")

        title_row = tk.Frame(left, bg="#2b2b2b")
        title_row.pack(fill="x")
        ttk.Label(title_row, text="Voice Active Prompter", style="Header.TLabel").pack(side="left")
        tk.Label(
            title_row,
            text="Workspace: Audio",
            bg="#2b2b2b",
            fg="#7f7f7f",
            font=("Helvetica", 9),
            padx=6,
        ).pack(side="right")
        self.left_intro_label = ttk.Label(
            left,
            text="선택한 마이크를 실시간 인식해서 대본을 자동 진행합니다.",
            style="Sub.TLabel",
        )
        self.left_intro_label.pack(anchor="w", fill="x", pady=(6, 18))

        control_card = ttk.Frame(left, style="Card.TFrame", padding=16)
        control_card.pack(fill="x", pady=(0, 14))
        tk.Frame(control_card, bg="#3a3a3a", height=1).pack(fill="x", pady=(0, 10))
        ttk.Label(control_card, text="DEVICE", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 10))

        device_row = ttk.Frame(control_card, style="Card.TFrame")
        device_row.pack(fill="x", pady=(0, 12))
        device_row.columnconfigure(0, weight=1)
        self.device_combo = ttk.Combobox(device_row, textvariable=self.device_var, state="readonly")
        self.device_combo.grid(row=0, column=0, sticky="ew")
        self.device_combo.bind("<<ComboboxSelected>>", self.on_device_selected)
        ttk.Button(device_row, text="새로고침", command=self.on_refresh_devices_button).grid(
            row=0, column=1, padx=(8, 0)
        )

        level_card = ttk.Frame(control_card, style="Card.TFrame")
        level_card.pack(fill="x", pady=(0, 12))
        ttk.Label(level_card, text="INPUT METER", style="PanelTitle.TLabel").pack(anchor="w")

        self.meter_action_wrap = ttk.Frame(level_card, style="Card.TFrame")
        self.meter_action_wrap.pack(fill="x", pady=(8, 0))

        self.meter_frame = ttk.Frame(self.meter_action_wrap, style="Card.TFrame")
        self.action_row = ttk.Frame(self.meter_action_wrap, style="Card.TFrame")
        self.meter_frame.pack(side="left", fill="y")
        self.action_row.pack(side="left", fill="both", expand=True, padx=(14, 0))

        self.level_bar = ttk.Progressbar(
            self.meter_frame,
            style="Level.Horizontal.TProgressbar",
            orient="horizontal",
            mode="determinate",
            maximum=100,
            variable=self.level_var,
            length=360,
        )
        self.level_bar.pack(fill="x", pady=(8, 6))
        self.level_meter_canvas = tk.Canvas(
            self.meter_frame,
            bg="#2b2b2b",
            width=74,
            height=188,
            highlightthickness=0,
            bd=0,
        )
        self.level_meter_canvas.pack(fill="x")
        self.level_meter_canvas.bind("<Configure>", lambda _event: self.draw_level_meter(self.level_var.get()))

        button_panel = tk.Frame(self.action_row, bg="#252526", bd=1, relief="flat")
        button_panel.pack(fill="both", expand=True)
        self.button_panel = button_panel
        for column in range(2):
            button_panel.columnconfigure(column, weight=1)
        for row in range(6):
            button_panel.rowconfigure(row, weight=1)
        self.start_button = ttk.Button(button_panel, text="시작", command=self.on_start_button)
        self.start_button.grid(row=0, column=0, sticky="ew")
        self.stop_button = ttk.Button(button_panel, text="중지", command=self.on_stop_button)
        self.stop_button.grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )
        self.reset_button = ttk.Button(button_panel, text="위치 초기화", command=self.on_reset_button)
        self.reset_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.presentation_start_button = ttk.Button(
            button_panel,
            text="프레젠테이션 시작",
            command=self.on_start_presentation_button,
        )
        self.presentation_start_button.grid(
            row=2, column=0, sticky="ew", pady=(8, 0)
        )
        self.presentation_stop_button = ttk.Button(
            button_panel,
            text="프레젠테이션 종료",
            command=self.on_stop_presentation_button,
        )
        self.presentation_stop_button.grid(
            row=2, column=1, sticky="ew", padx=(8, 0), pady=(8, 0)
        )
        self.presentation_mirror_button = ttk.Button(
            button_panel,
            text="프레젠테이션 좌우반전: OFF",
            command=self.toggle_presentation_mirror_button,
        )
        self.presentation_mirror_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.accuracy_priority_button = ttk.Button(
            button_panel,
            text="정확도 우선: OFF",
            command=self.toggle_accuracy_priority_button,
        )
        self.accuracy_priority_button.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.speed_priority_button = ttk.Button(
            button_panel,
            text="속도 우선: OFF",
            command=self.toggle_speed_priority_button,
        )
        self.speed_priority_button.grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        sensitivity_panel = tk.Frame(button_panel, bg="#252526")
        sensitivity_panel.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        sensitivity_panel.columnconfigure(0, weight=1)
        sensitivity_panel.columnconfigure(1, weight=0)
        tk.Label(
            sensitivity_panel,
            text="입력 민감도",
            bg="#252526",
            fg="#cfcfcf",
            font=("Helvetica", 9, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.input_sensitivity_value_label = tk.Label(
            sensitivity_panel,
            textvariable=self.input_sensitivity_display_var,
            bg="#1f1f1f",
            fg="#7ec8ff",
            font=("Helvetica", 10, "bold"),
            padx=10,
            pady=3,
        )
        self.input_sensitivity_value_label.grid(row=0, column=1, sticky="e")
        self.input_sensitivity_scale = tk.Scale(
            sensitivity_panel,
            from_=0.8,
            to=2.6,
            variable=self.input_sensitivity_var,
            orient="horizontal",
            resolution=0.01,
            showvalue=False,
            sliderlength=18,
            width=12,
            length=280,
            bd=0,
            highlightthickness=0,
            relief="flat",
            bg="#252526",
            fg="#cfcfcf",
            troughcolor="#161719",
            activebackground="#5aa9df",
            command=self.on_input_sensitivity_changed,
        )
        self.input_sensitivity_scale.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 0))

        script_card = ttk.Frame(left, style="Card.TFrame", padding=16)
        script_card.pack(fill="both", expand=True, pady=(0, 14))
        tk.Frame(script_card, bg="#3a3a3a", height=1).pack(fill="x", pady=(0, 10))
        ttk.Label(script_card, text="대본 편집", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Label(
            script_card,
            text="직접 입력하거나 파일을 가져와 수정할 수 있습니다. (.txt · Word .docx · 한글 .hwp)",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(0, 10))

        import_row = ttk.Frame(script_card, style="Card.TFrame")
        import_row.pack(fill="x", pady=(0, 10))
        self.load_button = ttk.Button(
            import_row,
            text="파일 가져오기…",
            command=self.import_script_file,
        )
        self.load_button.pack(side="left")

        self.script_editor = tk.Text(
            script_card,
            width=42,
            height=18,
            wrap="word",
            font=("Helvetica", 12),
            bg="#1f1f1f",
            fg="#e8e8e8",
            insertbackground="#f2f2f2",
            relief="flat",
            padx=14,
            pady=14,
        )
        self.script_editor.pack(fill="both", expand=True)
        self.script_editor.bind("<KeyRelease>", self._sync_teleprompter_from_editor)

        option_card = ttk.Frame(left, style="Card.TFrame", padding=16)
        option_card.pack(fill="x", pady=(0, 14))
        tk.Frame(option_card, bg="#3a3a3a", height=1).pack(fill="x", pady=(0, 10))
        ttk.Label(option_card, text="RECOGNITION", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 10))

        option_row = ttk.Frame(option_card, style="Card.TFrame")
        option_row.pack(fill="x", pady=(0, 12))
        option_row.columnconfigure(0, weight=1)
        option_row.columnconfigure(1, weight=1)
        option_row.columnconfigure(2, weight=1)
        ttk.Label(option_row, text="언어", style="Sub.TLabel").grid(row=0, column=0, sticky="w")
        self.language_combo = ttk.Combobox(
            option_row,
            textvariable=self.language_var,
            state="readonly",
            values=["ko", "en", "ja", "auto"],
        )
        self.language_combo.grid(row=1, column=0, sticky="ew", pady=(4, 0))

        ttk.Label(option_row, text="응답속도", style="Sub.TLabel").grid(
            row=0, column=1, sticky="w", padx=(18, 0)
        )
        ttk.Scale(
            option_row,
            from_=0.8,
            to=3.0,
            variable=self.chunk_seconds_var,
            orient="horizontal",
        ).grid(row=1, column=1, sticky="ew", padx=(18, 0), pady=(4, 0))

        ttk.Label(option_row, text="매칭 민감도", style="Sub.TLabel").grid(
            row=0, column=2, sticky="w", padx=(18, 0)
        )
        ttk.Scale(
            option_row,
            from_=0.1,
            to=0.7,
            variable=self.confidence_var,
            orient="horizontal",
        ).grid(row=1, column=2, sticky="ew", padx=(18, 0), pady=(4, 0))

        preset_row = ttk.Frame(option_card, style="Card.TFrame")
        preset_row.pack(fill="x", pady=(0, 10))
        ttk.Label(preset_row, text="프리셋", style="Sub.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(preset_row, text="속도 우선", command=self.apply_speed_preset).grid(
            row=1, column=0, sticky="ew", pady=(4, 0)
        )
        ttk.Button(preset_row, text="균형", command=self.apply_balanced_preset).grid(
            row=1, column=1, sticky="ew", padx=(8, 0), pady=(4, 0)
        )
        ttk.Button(preset_row, text="정확도 우선", command=self.apply_accuracy_preset).grid(
            row=1, column=2, sticky="ew", padx=(8, 0), pady=(4, 0)
        )
        for index in range(3):
            preset_row.columnconfigure(index, weight=1)

        subtitle_row = ttk.Frame(option_card, style="Card.TFrame")
        subtitle_row.pack(fill="x", pady=(0, 12))
        ttk.Checkbutton(
            subtitle_row,
            text="실시간 자막 사용",
            variable=self.subtitle_enabled_var,
            command=self.toggle_subtitle_feature,
            style="Dark.TCheckbutton",
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            subtitle_row,
            text="자막 좌우반전",
            variable=self.subtitle_mirror_var,
            command=self.toggle_subtitle_mirror,
            style="Dark.TCheckbutton",
        ).grid(row=0, column=1, sticky="w", padx=(16, 0))
        subtitle_row.columnconfigure(0, weight=1)
        subtitle_row.columnconfigure(1, weight=1)

        monitor_card = ttk.Frame(left, style="Card.TFrame", padding=16)
        monitor_card.pack(fill="x", pady=(0, 0))
        tk.Frame(monitor_card, bg="#3a3a3a", height=1).pack(fill="x", pady=(0, 10))
        ttk.Label(monitor_card, text="인식 · 상태", style="PanelTitle.TLabel").pack(anchor="w", pady=(0, 10))
        ttk.Label(monitor_card, text="실시간 인식 문장", style="Sub.TLabel").pack(anchor="w", pady=(0, 6))
        self.recognized_label = tk.Label(
            monitor_card,
            textvariable=self.recognized_var,
            justify="left",
            anchor="w",
            wraplength=420,
            bg="#1f1f1f",
            fg="#d7ba7d",
            padx=14,
            pady=14,
        )
        self.recognized_label.pack(fill="x")

        self.action_label = tk.Label(
            monitor_card,
            textvariable=self.last_action_var,
            justify="left",
            anchor="w",
            wraplength=420,
            bg="#1f1f1f",
            fg="#7ec8ff",
            padx=14,
            pady=10,
        )
        self.action_label.pack(fill="x", pady=(12, 0))

        self.listen_state_label = tk.Label(
            monitor_card,
            textvariable=self.listen_state_var,
            justify="left",
            anchor="w",
            wraplength=420,
            bg="#1f1f1f",
            fg="#8be08b",
            padx=14,
            pady=10,
        )
        self.listen_state_label.pack(fill="x", pady=(10, 0))

        self.status_label = tk.Label(
            monitor_card,
            textvariable=self.status_var,
            justify="left",
            anchor="w",
            wraplength=420,
            bg="#1f1f1f",
            fg="#8e8e8e",
            padx=14,
            pady=14,
        )
        self.status_label.pack(fill="x", pady=(12, 0))

        right = tk.Frame(right_shell, bg="#1e1e1e")
        right.pack(fill="both", expand=True)
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        program_tabs = tk.Frame(right, bg="#2d2d30", height=34)
        program_tabs.grid(row=0, column=0, sticky="ew")
        program_tabs.pack_propagate(False)
        tk.Label(
            program_tabs,
            text="Source",
            bg="#2d2d30",
            fg="#7d7d7d",
            font=("Helvetica", 10),
            padx=12,
        ).pack(side="left")
        tk.Label(
            program_tabs,
            text="Program",
            bg="#3a3a3a",
            fg="#f0f0f0",
            font=("Helvetica", 10, "bold"),
            padx=12,
            pady=6,
        ).pack(side="left")

        stage_header = tk.Frame(right, bg="#252526", height=38)
        stage_header.grid(row=1, column=0, sticky="ew")
        stage_header.pack_propagate(False)
        tk.Label(
            stage_header,
            text="LIVE TELEPROMPTER",
            bg="#252526",
            fg="#cccccc",
            font=("Helvetica", 11, "bold"),
            padx=12,
            pady=8,
        ).pack(anchor="w")

        viewer_shell = tk.Frame(right, bg="#0f0f10", bd=1, relief="flat")
        viewer_shell.grid(row=2, column=0, sticky="nsew")
        right.grid_rowconfigure(2, weight=1)

        self.teleprompter = tk.Text(
            viewer_shell,
            wrap="word",
            font=("Helvetica", 30),
            bg="#121212",
            fg="#f0f0f0",
            insertbackground="#f7f7f7",
            relief="flat",
            padx=110,
            pady=90,
            spacing3=18,
        )
        self.teleprompter.pack(fill="both", expand=True, padx=8, pady=8)
        self.teleprompter.tag_configure("active", foreground="#ffd166")
        self.teleprompter.tag_configure("past", foreground="#6a6a6a")
        self.teleprompter.tag_configure("focus", background="#202020")

        viewer_footer = tk.Frame(right, bg="#252526", height=30)
        viewer_footer.grid(row=3, column=0, sticky="ew")
        viewer_footer.pack_propagate(False)
        tk.Label(
            viewer_footer,
            text="Program Monitor",
            bg="#252526",
            fg="#8e8e8e",
            font=("Helvetica", 9),
            padx=10,
        ).pack(side="left")
        tk.Label(
            viewer_footer,
            text="Ready",
            bg="#252526",
            fg="#8e8e8e",
            font=("Helvetica", 9),
            padx=10,
        ).pack(side="right")

    def on_left_panel_resize(self, event) -> None:
        panel_width = max(event.width - 56, 220)
        wrap_width = max(panel_width - 36, 180)

        self.left_intro_label.configure(wraplength=wrap_width)
        self.recognized_label.configure(wraplength=wrap_width)
        self.action_label.configure(wraplength=wrap_width)
        self.listen_state_label.configure(wraplength=wrap_width)
        self.status_label.configure(wraplength=wrap_width)
        self.level_bar.configure(length=74)
        self.draw_level_meter(self.level_var.get())
        self.update_button_layout(panel_width)

    def mark_button_action(self, label: str) -> None:
        self.last_action_var.set(f"최근 버튼: {label} ({time.strftime('%H:%M:%S')})")

    def refresh_listening_ui(self) -> None:
        if self.is_listening:
            self.start_button.configure(state="disabled", text="실행 중")
            self.stop_button.configure(state="normal")
            self.listen_state_var.set("상태: 실시간 인식 ON")
            self.listen_state_label.configure(fg="#8be08b")
        else:
            self.start_button.configure(state="normal", text="시작")
            self.stop_button.configure(state="disabled")
            self.listen_state_var.set("상태: 대기")
            self.listen_state_label.configure(fg="#b8b8b8")

    def on_refresh_devices_button(self) -> None:
        self.mark_button_action("새로고침")
        self.refresh_devices()

    def on_start_button(self) -> None:
        self.mark_button_action("시작")
        self.start_listening()

    def on_stop_button(self) -> None:
        self.mark_button_action("중지")
        self.stop_listening()

    def on_reset_button(self) -> None:
        self.mark_button_action("위치 초기화")
        self.reset_progress()

    def on_start_presentation_button(self) -> None:
        self.mark_button_action("프레젠테이션 시작")
        self.start_presentation_mode()

    def on_stop_presentation_button(self) -> None:
        self.mark_button_action("프레젠테이션 종료")
        self.stop_presentation_mode()

    def toggle_presentation_mirror_button(self) -> None:
        self.subtitle_mirror_var.set(not self.subtitle_mirror_var.get())
        self.toggle_subtitle_mirror()

    def refresh_presentation_mirror_button(self) -> None:
        if not hasattr(self, "presentation_mirror_button"):
            return
        state = "ON" if self.subtitle_mirror_var.get() else "OFF"
        self.presentation_mirror_button.configure(text=f"프레젠테이션 좌우반전: {state}")

    def toggle_accuracy_priority_button(self) -> None:
        next_value = not self.accuracy_priority_var.get()
        self.accuracy_priority_var.set(next_value)
        if next_value and self.speed_priority_var.get():
            self.speed_priority_var.set(False)
        self.apply_priority_modes()
        self.mark_button_action(f"정확도 우선 {'ON' if next_value else 'OFF'}")

    def toggle_speed_priority_button(self) -> None:
        next_value = not self.speed_priority_var.get()
        self.speed_priority_var.set(next_value)
        if next_value and self.accuracy_priority_var.get():
            self.accuracy_priority_var.set(False)
        self.apply_priority_modes()
        self.mark_button_action(f"속도 우선 {'ON' if next_value else 'OFF'}")

    def refresh_priority_mode_buttons(self) -> None:
        if not hasattr(self, "accuracy_priority_button") or not hasattr(self, "speed_priority_button"):
            return
        accuracy_state = "ON" if self.accuracy_priority_var.get() else "OFF"
        speed_state = "ON" if self.speed_priority_var.get() else "OFF"
        self.accuracy_priority_button.configure(text=f"정확도 우선: {accuracy_state}")
        self.speed_priority_button.configure(text=f"속도 우선: {speed_state}")

    def get_gate_thresholds(self) -> tuple[float, float]:
        # Higher sensitivity lowers the gate so quieter speech passes through.
        sensitivity = max(0.8, float(self.input_sensitivity_var.get()))
        return 0.0025 / sensitivity, 0.0007 / sensitivity

    def refresh_input_sensitivity_display(self) -> None:
        percent = int(round(float(self.input_sensitivity_var.get()) * 100))
        self.input_sensitivity_display_var.set(f"{percent}%")

    def on_input_sensitivity_changed(self, _value=None) -> None:
        self.refresh_input_sensitivity_display()
        if self.transcriber is None:
            return
        gate_peak, gate_rms = self.get_gate_thresholds()
        self.transcriber.gate_peak_threshold = gate_peak
        self.transcriber.gate_rms_threshold = gate_rms

    def get_decoder_preferences(self) -> tuple[int, int]:
        if self.accuracy_priority_var.get():
            return 6, 6
        if self.speed_priority_var.get():
            return 1, 1
        return 3, 3

    def apply_priority_modes(self) -> None:
        self.refresh_priority_mode_buttons()
        beam_size, best_of = self.get_decoder_preferences()
        if self.transcriber is not None:
            self.transcriber.beam_size = beam_size
            self.transcriber.best_of = best_of
        mode = "균형"
        if self.accuracy_priority_var.get():
            mode = "정확도 우선"
        elif self.speed_priority_var.get():
            mode = "속도 우선"
        self.set_status(f"우선 모드 적용: {mode} (beam={beam_size}, best_of={best_of})")

    def apply_speed_preset(self) -> None:
        self.apply_recognition_preset("속도 우선", chunk_seconds=0.9, confidence=0.18, backtrack_chars=340)

    def apply_balanced_preset(self) -> None:
        self.apply_recognition_preset("균형", chunk_seconds=1.4, confidence=0.22, backtrack_chars=300)

    def apply_accuracy_preset(self) -> None:
        self.apply_recognition_preset("정확도 우선", chunk_seconds=2.1, confidence=0.30, backtrack_chars=220)

    def apply_recognition_preset(
        self,
        name: str,
        chunk_seconds: float,
        confidence: float,
        backtrack_chars: int,
    ) -> None:
        self.recognition_preset_var.set(name)
        self.chunk_seconds_var.set(chunk_seconds)
        self.confidence_var.set(confidence)
        self.progress_window_var.set(backtrack_chars)

        if self.transcriber is not None:
            self.transcriber.chunk_seconds = max(float(self.chunk_seconds_var.get()), 0.8)
        self.apply_priority_modes()

        self.mark_button_action(f"프리셋: {name}")
        self.set_status(
            f"인식 프리셋 적용: {name} (응답속도 {chunk_seconds:.1f}s, 민감도 {confidence:.2f})"
        )

    def update_button_layout(self, panel_width: int) -> None:
        self.button_panel.columnconfigure(0, weight=1, minsize=max(panel_width // 4, 96))
        self.button_panel.columnconfigure(1, weight=1, minsize=max(panel_width // 4, 96))

    def refresh_devices(self) -> None:
        self.devices = []
        names: list[str] = []

        try:
            for index, device in enumerate(sd.query_devices()):
                if int(device["max_input_channels"]) < 1:
                    continue
                entry = {
                    "id": index,
                    "name": f'{device["name"]} ({int(device["default_samplerate"])} Hz)',
                    "samplerate": int(device["default_samplerate"]) or 16000,
                }
                self.devices.append(entry)
                names.append(str(entry["name"]))
        except Exception as exc:
            self.set_status(f"마이크 목록을 불러오지 못했습니다: {exc}")
            return

        self.device_combo["values"] = names
        if names:
            self.device_combo.current(0)
            self.device_var.set(names[0])
            self.start_level_monitor()
            self.set_status(f"마이크 {len(names)}개를 찾았습니다.")
            self.ensure_auto_listening(restart=False)
        else:
            self.device_var.set("")
            self.stop_level_monitor()
            self.set_status("사용 가능한 입력 마이크를 찾지 못했습니다.")

    def on_device_selected(self, _event=None) -> None:
        self.start_level_monitor()
        self.ensure_auto_listening(restart=True)

    def ensure_auto_listening(self, restart: bool = False) -> None:
        if not self.auto_listen_on_device_select:
            return

        if restart and self.transcriber is not None:
            self.stop_listening()

        if self.transcriber is None:
            self.start_listening()

    def get_selected_device(self) -> dict[str, object] | None:
        if not self.devices or not self.device_var.get():
            return None
        return next(
            (device for device in self.devices if device["name"] == self.device_var.get()),
            None,
        )

    def start_level_monitor(self) -> None:
        selected = self.get_selected_device()
        if selected is None:
            self.update_input_level(0.0)
            return

        if self.monitor_stream is not None:
            try:
                self.monitor_stream.stop()
                self.monitor_stream.close()
            except Exception:
                pass
            self.monitor_stream = None

        sample_rate = int(selected["samplerate"]) or 16000
        sample_rate = max(sample_rate, 16000)

        try:
            self.monitor_stream = sd.InputStream(
                device=int(selected["id"]),
                channels=1,
                samplerate=sample_rate,
                dtype="float32",
                latency="low",
                blocksize=max(int(sample_rate * 0.08), 512),
                callback=self._level_monitor_callback,
            )
            self.monitor_stream.start()
        except Exception as exc:
            self.monitor_stream = None
            self.update_input_level(0.0)
            self.set_status(f"입력 레벨 모니터를 시작하지 못했습니다: {exc}")

    def stop_level_monitor(self, reset_meter: bool = True) -> None:
        if self.monitor_stream is not None:
            try:
                self.monitor_stream.stop()
                self.monitor_stream.close()
            except Exception:
                pass
            self.monitor_stream = None
        if reset_meter:
            self.update_input_level(0.0)

    def _level_monitor_callback(self, indata, frames, time, status) -> None:  # noqa: ARG002
        if status:
            return
        mono = indata.astype(np.float32)
        rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-8)
        peak = float(np.max(np.abs(mono)) + 1e-8)
        # Use peak-biased metering so live speech feels responsive like an NLE audio meter.
        db = 20.0 * np.log10(max(peak, rms * 1.8, 1e-8))
        self.queue_level_update(self.db_to_meter_value(db))

    def queue_level_update(self, level: float) -> None:
        try:
            self.level_queue.put_nowait(level)
        except queue.Full:
            pass

    @staticmethod
    def db_to_meter_value(db: float) -> float:
        floor_db = -60.0
        clipped = max(floor_db, min(0.0, db))
        return ((clipped - floor_db) / abs(floor_db)) * 100.0

    def process_level_queue(self) -> None:
        latest_level: float | None = None
        while True:
            try:
                latest_level = self.level_queue.get_nowait()
            except queue.Empty:
                break

        if latest_level is not None:
            self.update_input_level(latest_level)

        if self.root.winfo_exists():
            self.root.after(50, self.process_level_queue)

    def _sync_teleprompter_from_editor(self, _event=None) -> None:
        self.render_teleprompter("")

    def apply_script(self, script: str) -> None:
        self.script_editor.delete("1.0", "end")
        self.script_editor.insert("1.0", script)
        self.current_index = 0
        self.render_teleprompter("")

    def get_script(self) -> str:
        return self.script_editor.get("1.0", "end-1c")

    def render_teleprompter(self, recognized_text: str) -> None:
        script = self.get_script()
        current_index = max(0, min(self.current_index, len(script)))

        self.teleprompter.config(state="normal")
        self.teleprompter.delete("1.0", "end")
        self.teleprompter.insert("1.0", script)
        self.teleprompter.tag_remove("active", "1.0", "end")
        self.teleprompter.tag_remove("past", "1.0", "end")
        self.teleprompter.tag_remove("focus", "1.0", "end")

        if current_index > 0:
            self.teleprompter.tag_add("past", "1.0", f"1.0+{current_index}c")

        if recognized_text:
            end_index = min(len(script), current_index + max(len(recognized_text), 1))
            self.teleprompter.tag_add(
                "active",
                f"1.0+{current_index}c",
                f"1.0+{end_index}c",
            )
            focus_end = min(len(script), end_index + 120)
            self.teleprompter.tag_add(
                "focus",
                f"1.0+{current_index}c",
                f"1.0+{focus_end}c",
            )

        self.teleprompter.see(f"1.0+{max(current_index - 80, 0)}c")
        self.teleprompter.config(state="disabled")

    def start_listening(self) -> None:
        if self.transcriber is not None:
            self.set_status("이미 실시간 인식이 실행 중입니다.")
            self.recognized_var.set(LISTENING_MESSAGE)
            self.sync_subtitle_preview()
            self.is_listening = True
            self.refresh_listening_ui()
            return

        if not self.devices or not self.device_var.get():
            self.set_status("먼저 사용할 마이크를 선택해주세요.")
            return

        script = self.get_script().strip()
        if not script:
            self.set_status("대본이 비어 있습니다.")
            return

        selected = self.get_selected_device()
        if selected is None:
            self.set_status("선택한 마이크를 찾을 수 없습니다.")
            return

        sample_rate = int(selected["samplerate"])
        if sample_rate < 16000:
            sample_rate = 16000

        try:
            self.stop_level_monitor(reset_meter=False)
            self.transcriber = LiveMicTranscriber(
                device_id=int(selected["id"]),
                sample_rate=sample_rate,
                on_level=self.queue_level_update,
                chunk_seconds=max(float(self.chunk_seconds_var.get()), 0.8),
                language=None if self.language_var.get() == "auto" else self.language_var.get(),
                beam_size=self.get_decoder_preferences()[0],
                best_of=self.get_decoder_preferences()[1],
                gate_peak_threshold=self.get_gate_thresholds()[0],
                gate_rms_threshold=self.get_gate_thresholds()[1],
                initial_prompt=(
                    "텔레프롬프터 대본을 읽는 발표자의 또렷한 한국어 음성이다. "
                    "발음이 자연스럽지 않아도 대본 문맥에 맞춰 가장 정확한 문장으로 복원한다."
                ),
                script_prompt_provider=self.get_script_prompt_hint,
                on_text=lambda text: self.root.after(0, self.handle_recognized_text, text),
                on_status=lambda text: self.root.after(0, self.set_status, text),
            )
            self.transcriber.start()
            self.recognized_var.set(LISTENING_MESSAGE)
            self.sync_subtitle_preview()
            self.is_listening = True
            self.refresh_listening_ui()
        except Exception as exc:
            self.transcriber = None
            self.start_level_monitor()
            self.set_status(f"실시간 인식을 시작하지 못했습니다: {exc}")
            self.recognized_var.set("인식을 시작하지 못했습니다. 상태 메시지를 확인하세요.")
            self.sync_subtitle_preview()
            self.is_listening = False
            self.refresh_listening_ui()

    def stop_listening(self) -> None:
        if self.transcriber is None:
            self.set_status("현재 실행 중인 인식이 없습니다.")
            self.is_listening = False
            self.refresh_listening_ui()
            return
        self.transcriber.stop()
        self.transcriber = None
        self.recognized_var.set("인식을 중지했습니다.")
        self.sync_subtitle_preview()
        self.start_level_monitor()
        self.is_listening = False
        self.refresh_listening_ui()

    def handle_recognized_text(self, text: str) -> None:
        self.recognized_var.set(text)
        self.sync_subtitle_preview()

        script = self.get_script()
        matched_index, _, confidence = find_progressive_match(
            script=script,
            recognized_text=text,
            current_index=self.current_index,
            backtrack_chars=int(self.progress_window_var.get()),
        )

        if confidence >= float(self.confidence_var.get()) and matched_index >= self.current_index - 24:
            # Jump slightly past the matched region so the next line is ready sooner.
            self.current_index = max(self.current_index, matched_index + max(len(text) // 3, 1))

        self.render_teleprompter(text)
        self.set_status(f"인식 완료, confidence={confidence:.2f}, index={self.current_index}")

    def get_script_prompt_hint(self) -> str:
        script = self.get_script()
        if not script.strip():
            return ""

        start = max(self.current_index - 80, 0)
        end = min(self.current_index + 220, len(script))
        return f"현재 읽는 대본 문맥: {script[start:end]}"

    def reset_progress(self) -> None:
        self.current_index = 0
        self.recognized_var.set("진행 위치를 초기화했습니다.")
        self.render_teleprompter("")
        self.set_status("처음 위치로 되돌렸습니다.")

    def import_script_file(self) -> None:
        file_path = filedialog.askopenfilename(
            title="대본 파일 가져오기",
            filetypes=[
                ("모든 지원 형식", "*.txt *.docx *.docs *.hwp"),
                ("Word / Google 문서", "*.docx *.docs"),
                ("한글", "*.hwp"),
                ("텍스트", "*.txt"),
                ("모든 파일", "*.*"),
            ],
        )
        if not file_path:
            return

        path = Path(file_path)
        try:
            script = extract_text_from_path(path)
        except ValueError as exc:
            self.set_status(str(exc))
            return
        except Exception as exc:
            self.set_status(f"대본을 읽지 못했습니다: {exc}")
            return

        if not script.strip():
            self.set_status("파일에서 추출한 텍스트가 비어 있습니다.")
            return

        self.apply_script(script)
        self.set_status(f"편집창에 불러왔습니다: {path.name} ({path.suffix.lower()}) — 수정 후 그대로 사용하세요.")

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def current_subtitle_seed_text(self) -> str:
        text = self.recognized_var.get().strip()
        if text and text != INITIAL_RECOGNIZED_MESSAGE:
            return text
        if self.transcriber is not None:
            return LISTENING_MESSAGE
        return "곧 인식된 문장이 여기에 표시됩니다."

    def sync_subtitle_preview(self) -> None:
        if self.subtitle_enabled_var.get() and self.subtitle_window.is_visible:
            self.subtitle_window.update_text(self.current_subtitle_seed_text())

    def update_input_level(self, level: float) -> None:
        clamped = max(0.0, min(level, 100.0))
        # Smooth rising/falling motion so the meter feels stable while staying responsive.
        rise = 0.58
        fall = 0.24
        factor = rise if clamped >= self.display_level else fall
        self.display_level = (self.display_level * (1.0 - factor)) + (clamped * factor)

        now = time.monotonic()
        if self.display_level >= self.peak_hold_level:
            self.peak_hold_level = self.display_level
            self.peak_hold_time = now
        elif now - self.peak_hold_time > 0.9:
            self.peak_hold_level = max(self.display_level, self.peak_hold_level - 2.2)

        self.level_var.set(self.display_level)
        self.draw_level_meter(self.display_level)

    def draw_level_meter(self, level: float) -> None:
        canvas = self.level_meter_canvas
        width = max(canvas.winfo_width(), 74)
        height = max(canvas.winfo_height(), 188)
        canvas.delete("all")

        segment_count = self.level_meter_segments
        gap = 2
        meter_gap = 6
        label_space = 16
        bottom_pad = 10
        top_pad = 8
        usable_height = height - top_pad - bottom_pad
        segment_height = max((usable_height - (gap * (segment_count - 1))) / segment_count, 3)
        lane_width = max((width - label_space - meter_gap - 12) / 2, 12)
        left_x0 = label_space + 4
        left_x1 = left_x0 + lane_width
        right_x0 = left_x1 + meter_gap
        right_x1 = right_x0 + lane_width
        filled = int(round((max(0.0, min(level, 100.0)) / 100.0) * segment_count))
        peak_segment = int(round((max(0.0, min(self.peak_hold_level, 100.0)) / 100.0) * segment_count))

        canvas.create_rectangle(0, 0, width, height, fill="#1b1b1d", outline="#111214")

        tick_values = [0, -6, -12, -24, -48]
        for tick_db in tick_values:
            tick_level = self.db_to_meter_value(float(tick_db))
            tick_index = (tick_level / 100.0) * segment_count
            y = height - bottom_pad - (tick_index * (segment_height + gap))
            canvas.create_line(0, y, width, y, fill="#2e2f33")
            canvas.create_text(2, y, text=str(tick_db), fill="#7c7d81", font=("Menlo", 7), anchor="w")

        for index in range(segment_count):
            y1 = height - bottom_pad - (index * (segment_height + gap))
            y0 = y1 - segment_height

            if index < segment_count * 0.55:
                active_color = "#52d273"
            elif index < segment_count * 0.83:
                active_color = "#e4c64b"
            else:
                active_color = "#ef5b5b"

            is_active = index < filled
            fill_color = active_color if is_active else "#2a2b2f"
            outline_color = "#111214"

            for x0, x1 in ((left_x0, left_x1), (right_x0, right_x1)):
                canvas.create_rectangle(
                    x0,
                    y0,
                    x1,
                    y1,
                    fill=fill_color,
                    outline=outline_color,
                    width=1,
                )

        if peak_segment > 0:
            peak_y1 = height - bottom_pad - ((peak_segment - 1) * (segment_height + gap))
            peak_y0 = peak_y1 - 2
            for x0, x1 in ((left_x0, left_x1), (right_x0, right_x1)):
                canvas.create_rectangle(
                    x0,
                    peak_y0,
                    x1,
                    peak_y1,
                    fill="#f1f1f1",
                    outline="#f1f1f1",
                    width=0,
                )

        canvas.create_text(left_x0 + (lane_width / 2), height - 2, text="L", fill="#8b8c90", font=("Menlo", 8), anchor="s")
        canvas.create_text(right_x0 + (lane_width / 2), height - 2, text="R", fill="#8b8c90", font=("Menlo", 8), anchor="s")

    def toggle_subtitle_feature(self) -> None:
        if not self.subtitle_enabled_var.get():
            self.stop_presentation_mode()

    def toggle_subtitle_mirror(self) -> None:
        self.subtitle_window.set_mirrored(self.subtitle_mirror_var.get())
        self.refresh_presentation_mirror_button()
        if self.subtitle_window.is_visible:
            self.sync_subtitle_preview()

    def start_presentation_mode(self) -> None:
        if not self.subtitle_enabled_var.get():
            self.subtitle_enabled_var.set(True)
        if self.transcriber is None:
            self.start_listening()
        self.subtitle_window.set_mirrored(self.subtitle_mirror_var.get())
        self.subtitle_window.update_text(self.current_subtitle_seed_text())
        self.subtitle_window.enter_presentation_mode()
        self.set_status("프레젠테이션 모드를 시작했습니다. Esc로 자막 창을 닫을 수 있습니다.")

    def stop_presentation_mode(self) -> None:
        self.subtitle_window.exit_presentation_mode()
        self.set_status("프레젠테이션 모드를 종료했습니다.")

    def on_close(self) -> None:
        if self.transcriber is not None:
            self.transcriber.stop()
            self.transcriber = None
        self.stop_level_monitor()
        self.subtitle_window.window.destroy()
        self.root.destroy()

    def _ensure_main_window_visible(self) -> None:
        # macOS 환경에서 창이 첫 실행 시 뒤로 숨어 보이지 않는 경우를 방지한다.
        def _focus() -> None:
            if not self.root.winfo_exists():
                return
            try:
                self.root.state("normal")
            except tk.TclError:
                pass
            try:
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()
                self.root.attributes("-topmost", True)
                self.root.after(140, lambda: self.root.attributes("-topmost", False))
            except tk.TclError:
                return

        self.root.after(120, _focus)
        self.root.after(600, _focus)


def run() -> None:
    root = tk.Tk()
    TeleprompterDesktopApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()
