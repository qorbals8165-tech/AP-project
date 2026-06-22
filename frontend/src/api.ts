import type { HealthResponse, ProgressResponse, UiSettings } from "./types";

// 개발 중엔 별도 백엔드(8000)를 가리키고, 프로덕션 빌드(독립 실행)에서는 동일 서버의 /api를 사용
const API_BASE = (
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
  (import.meta.env.DEV ? "http://localhost:8000/api" : "/api")
).replace(/\/$/, "");

const API_KEY = (import.meta.env.VITE_API_KEY as string | undefined) || "";

function authHeaders(): Record<string, string> {
  return API_KEY ? { "X-API-Key": API_KEY } : {};
}

export async function fetchDefaults(): Promise<UiSettings> {
  const response = await fetch(`${API_BASE}/settings/defaults`, {
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error("Failed to load settings");

  const data = await response.json();
  return {
    fontSize: data.font_size,
    lineHeight: data.line_height,
    scrollSpeed: data.scroll_speed,
    contentWidth: data.content_width,
    theme: data.theme,
    fontFamily: data.font_family,
    fontWeight: data.font_weight ?? 400,
    textColor: data.text_color ?? "#ffffff",
    bgColor: data.bg_color ?? "#1b1410",
  };
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) throw new Error("Failed to load backend status");
  return response.json();
}

export async function estimateProgress(
  script: string,
  recognizedText: string
): Promise<ProgressResponse> {
  const response = await fetch(`${API_BASE}/progress`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ script, recognized_text: recognizedText }),
  });
  if (!response.ok) throw new Error("Failed to estimate script progress");
  return response.json();
}

export async function transcribeAudio(file: File, initialPrompt?: string): Promise<string> {
  const body = new FormData();
  body.append("file", file);
  if (initialPrompt?.trim()) body.append("initial_prompt", initialPrompt.trim());

  const response = await fetch(`${API_BASE}/transcribe`, {
    method: "POST",
    headers: authHeaders(),
    body,
  });
  if (!response.ok) throw new Error("Failed to transcribe audio");

  const data = await response.json();
  return data.text;
}

export interface AudioDevice {
  index: number;
  name: string;
  default: boolean;
}

export interface RecognitionState {
  running: boolean;
  level: number;
  text: string;
  seq: number;
}

export async function fetchAudioDevices(): Promise<AudioDevice[]> {
  const response = await fetch(`${API_BASE}/audio-devices`, { headers: authHeaders() });
  if (!response.ok) throw new Error("Failed to load audio devices");
  const data = await response.json();
  return (data.devices ?? []) as AudioDevice[];
}

export async function fetchSystemFonts(): Promise<string[]> {
  const response = await fetch(`${API_BASE}/system-fonts`, { headers: authHeaders() });
  if (!response.ok) throw new Error("Failed to load system fonts");
  const data = await response.json();
  return (data.fonts ?? []) as string[];
}

export async function startRecognition(
  deviceIndex: number | null,
  options: { transcribe: boolean; script?: string }
): Promise<RecognitionState> {
  const response = await fetch(`${API_BASE}/recognition/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      device_index: deviceIndex,
      transcribe: options.transcribe,
      script: options.script ?? "",
    }),
  });
  if (!response.ok) {
    const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    throw new Error((data.detail as string) || "마이크를 시작할 수 없습니다.");
  }
  return response.json();
}

export async function stopRecognition(): Promise<RecognitionState> {
  const response = await fetch(`${API_BASE}/recognition/stop`, {
    method: "POST",
    headers: authHeaders(),
  });
  if (!response.ok) throw new Error("Failed to stop recognition");
  return response.json();
}

export async function fetchRecognitionState(): Promise<RecognitionState> {
  const response = await fetch(`${API_BASE}/recognition/state`, { headers: authHeaders() });
  if (!response.ok) throw new Error("Failed to fetch recognition state");
  return response.json();
}

export async function updateRecognitionScript(script: string): Promise<void> {
  await fetch(`${API_BASE}/recognition/script`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ script }),
  }).catch(() => undefined);
}

export async function importDocument(file: File): Promise<string> {
  const body = new FormData();
  body.append("file", file);

  const response = await fetch(`${API_BASE}/import-document`, {
    method: "POST",
    headers: authHeaders(),
    body,
  });

  if (!response.ok) {
    const data = await response.json().catch(() => ({})) as Record<string, unknown>;
    throw new Error((data.detail as string) || "문서를 가져오지 못했습니다.");
  }

  const data = await response.json() as Record<string, unknown>;
  return data.text as string;
}
