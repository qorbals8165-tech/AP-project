"""OS 레벨(sounddevice) 마이크 탐색 · 레벨 측정 · Whisper 인식.

브라우저 getUserMedia 대신 PortAudio로 직접 캡처하므로 권한 팝업이 없고,
프로그램 시작과 동시에 연결된 입력 장치를 모두 나열할 수 있다.
"""

from __future__ import annotations

import math
import queue
import tempfile
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

from .core import transcribe_audio_file_with_options


def list_input_devices() -> list[dict[str, object]]:
    import sounddevice as sd

    try:
        default_index = sd.default.device[0]
    except Exception:
        default_index = None

    devices: list[dict[str, object]] = []
    for index, dev in enumerate(sd.query_devices()):
        if dev.get("max_input_channels", 0) > 0:
            devices.append(
                {
                    "index": index,
                    "name": dev.get("name", f"마이크 {index}"),
                    "default": index == default_index,
                }
            )
    return devices


def _rms_to_percent(rms: float) -> float:
    db = 20.0 * math.log10(max(rms, 1e-8))
    return max(0.0, min(100.0, ((db + 60.0) / 60.0) * 100.0))


class RecognitionController:
    """단일 마이크 스트림을 관리하는 싱글턴 컨트롤러."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stream = None
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._audio_q: "queue.Queue[np.ndarray]" = queue.Queue()
        self.sample_rate = 16000
        # 음성 구간 검출(VAD) 파라미터
        self.end_silence = 0.35   # 발화 끝으로 보는 침묵 길이(초)
        self.min_speech = 0.5     # 전사를 시작할 최소 발화 길이(초)
        self.max_segment = 12.0   # 이만큼 차면 강제 전사(초)
        self.interim_seconds = 0.8  # 말하는 도중 중간 전사 간격(초)
        self.gate_peak = 0.0014
        self.gate_rms = 0.0004

        # 외부에서 폴링하는 상태
        self.running = False
        self.level = 0.0
        self.text = ""
        self.seq = 0  # 새 인식 결과가 나올 때마다 증가
        self.transcribe = False
        self.script_hint = ""

    # ── 제어 ────────────────────────────────────────────────────────────
    def start(self, device_index: int | None, *, transcribe: bool, script: str = "") -> None:
        import sounddevice as sd

        with self._lock:
            self._stop_locked()
            self._stop = threading.Event()
            self._audio_q = queue.Queue()
            self.level = 0.0
            self.text = ""
            self.transcribe = transcribe
            self.script_hint = script or ""

            self._stream = sd.InputStream(
                device=device_index,
                channels=1,
                samplerate=self.sample_rate,
                dtype="float32",
                latency="low",
                blocksize=max(int(self.sample_rate * 0.12), 1024),
                callback=self._on_audio,
            )
            self._stream.start()
            self.running = True

            if transcribe:
                # 모델을 미리 로드해 첫 청크 지연을 줄인다.
                threading.Thread(target=self._warm_model, daemon=True).start()
                self._worker = threading.Thread(target=self._worker_loop, daemon=True)
                self._worker.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=1.0)
        self._worker = None
        self.running = False
        self.level = 0.0

    def state(self) -> dict[str, object]:
        return {
            "running": self.running,
            "level": round(self.level, 1),
            "text": self.text,
            "seq": self.seq,
        }

    def set_script(self, script: str) -> None:
        self.script_hint = script or ""

    # ── 오디오 콜백 / 워커 ────────────────────────────────────────────────
    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        rms = float(np.sqrt(np.mean(np.square(indata))) + 1e-8)
        self.level = _rms_to_percent(rms)
        if self.transcribe:
            self._audio_q.put(indata.copy())

    def _worker_loop(self) -> None:
        """음성 구간 검출(VAD) + 중간 전사: 말하는 도중에도 주기적으로 전사해
        줄을 읽는 중간에 넘어갈 수 있게 한다(반응성). 침묵/잡음만 있을 땐 전사하지
        않아 Whisper 환각을 막는다.
        """
        import time

        sr = self.sample_rate
        seg = np.empty((0, 1), dtype=np.float32)
        silence_run = 0.0      # 발화 뒤 이어진 침묵(초)
        speech_blocks = 0      # 현재 segment의 말소리 블록 수
        had_speech = False
        noise_floor = 0.005    # 적응형 잡음 바닥
        tail_text = ""         # 직전까지 확정된 최근 텍스트(줄이 여러 발화에 걸칠 때)
        last_interim = 0.0     # 마지막 중간 전사 시각
        MIN_SPEECH_BLOCKS = 3  # 진짜 발화로 인정할 최소 말소리 블록(잡음 스파이크 무시)

        def publish(seg_text: str) -> None:
            merged = self._merge(tail_text, seg_text)[-200:] if tail_text else seg_text
            if merged and merged != self.text:
                self.text = merged
                self.seq += 1

        while not self._stop.is_set():
            try:
                chunk = self._audio_q.get(timeout=0.2)
            except queue.Empty:
                continue

            block_rms = float(np.sqrt(np.mean(np.square(chunk))) + 1e-8)
            block_sec = len(chunk) / sr
            seg = np.concatenate((seg, chunk), axis=0)

            speech_th = max(0.007, noise_floor * 2.5)
            if block_rms >= speech_th:
                had_speech = True
                speech_blocks += 1
                silence_run = 0.0
            else:
                silence_run += block_sec
                noise_floor = 0.95 * noise_floor + 0.05 * block_rms

            # 잡음 스파이크 한두 번은 발화로 보지 않음
            real_speech = speech_blocks >= MIN_SPEECH_BLOCKS
            seg_sec = len(seg) / sr
            now = time.monotonic()
            end_of_utterance = real_speech and silence_run >= self.end_silence and seg_sec >= self.min_speech
            force = real_speech and seg_sec >= self.max_segment
            interim = real_speech and seg_sec >= 1.0 and (now - last_interim) >= self.interim_seconds

            if end_of_utterance or force:
                seg_text = self._transcribe(seg)
                if seg_text:
                    tail_text = self._merge(tail_text, seg_text)[-200:]
                    if tail_text != self.text:
                        self.text = tail_text
                        self.seq += 1
                seg = seg[-int(sr * 0.2):]
                silence_run = 0.0
                speech_blocks = 0
                had_speech = False
                last_interim = now
            elif interim:
                last_interim = now
                seg_text = self._transcribe(seg)
                if seg_text:
                    publish(seg_text)
            elif not had_speech and seg_sec > 1.0:
                seg = seg[-int(sr * 0.3):]
                speech_blocks = 0

    def _transcribe(self, audio: np.ndarray) -> str:
        prepared = self._prepare(audio)
        if prepared is None:
            return ""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp_path = Path(tmp.name)
        try:
            sf.write(tmp_path, prepared.squeeze(), self.sample_rate)
            result = transcribe_audio_file_with_options(
                tmp_path,
                language="ko",
                initial_prompt=None,  # 이 모델은 prompt_ids 사용 시 시작부 오인식 → 미사용
            )
            text = str(result.get("text", "")).strip()
            return "" if self._is_hallucination(text) else text
        except Exception:
            return ""
        finally:
            tmp_path.unlink(missing_ok=True)

    # Whisper가 침묵/잡음에서 흔히 지어내는 문구 (정규화 비교)
    _HALLUCINATION = {
        "시청해주셔서감사합니다", "구독과좋아요부탁드립니다", "다음영상에서만나요",
        "오늘도시청해주셔서감사합니다", "지금까지시청해주셔서감사합니다", "한글자막by",
        "끝까지시청해주셔서감사합니다", "thankyou", "thanksforwatching",
        "시청해주셔서감사합니다다음영상에서만나요",
    }

    @classmethod
    def _is_hallucination(cls, text: str) -> bool:
        if not text:
            return True
        norm = "".join(text.lower().split()).strip(".!?…,~ ")
        if not norm:
            return True
        return norm in cls._HALLUCINATION

    @staticmethod
    def _merge(previous: str, current: str) -> str:
        """직전 누적 텍스트와 새 청크 텍스트의 겹치는 꼬리를 합쳐 이어붙인다."""
        if not previous:
            return current
        lo_prev = previous.lower()
        lo_cur = current.lower()
        max_overlap = min(len(lo_prev), len(lo_cur), 40)
        for size in range(max_overlap, 4, -1):
            if lo_prev.endswith(lo_cur[:size]):
                return previous + current[size:]
        return f"{previous} {current}".strip()

    def _warm_model(self) -> None:
        try:
            from .core import get_transformers_components

            get_transformers_components()
        except Exception:
            pass

    def _prepare(self, audio: np.ndarray) -> np.ndarray | None:
        mono = audio.squeeze().astype(np.float32)
        if mono.size == 0:
            return None
        mono = mono - float(np.mean(mono))
        peak = float(np.max(np.abs(mono)))
        rms = float(np.sqrt(np.mean(np.square(mono))) + 1e-8)
        if peak < self.gate_peak and rms < self.gate_rms:
            return None
        gain = min(0.92 / max(peak, 1e-6), 8.0)
        return np.clip(mono * gain, -1.0, 1.0).reshape(-1, 1)


controller = RecognitionController()
