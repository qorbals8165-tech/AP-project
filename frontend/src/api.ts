import type { HealthResponse, ProgressResponse, UiSettings } from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000/api").replace(
  /\/$/,
  ""
);

export async function fetchDefaults(): Promise<UiSettings> {
  const response = await fetch(`${API_BASE}/settings/defaults`);
  if (!response.ok) {
    throw new Error("Failed to load settings");
  }

  const data = await response.json();
  return {
    fontSize: data.font_size,
    lineHeight: data.line_height,
    scrollSpeed: data.scroll_speed,
    contentWidth: data.content_width,
    theme: data.theme,
    fontFamily: data.font_family,
  };
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) {
    throw new Error("Failed to load backend status");
  }
  return response.json();
}

export async function estimateProgress(
  script: string,
  recognizedText: string
): Promise<ProgressResponse> {
  const response = await fetch(`${API_BASE}/progress`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      script,
      recognized_text: recognizedText,
    }),
  });

  if (!response.ok) {
    throw new Error("Failed to estimate script progress");
  }

  return response.json();
}

export async function transcribeAudio(file: File): Promise<string> {
  const body = new FormData();
  body.append("file", file);

  const response = await fetch(`${API_BASE}/transcribe`, {
    method: "POST",
    body,
  });

  if (!response.ok) {
    throw new Error("Failed to transcribe audio");
  }

  const data = await response.json();
  return data.text;
}
