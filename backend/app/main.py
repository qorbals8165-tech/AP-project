from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .core import find_best_match, get_runtime_device, get_runtime_model_name, transcribe_audio_file
from .settings import get_csv_env
from .schemas import ProgressRequest, ProgressResponse, SettingsResponse


APP_TITLE = "Voice Active Prompter API"
MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "small")
MODEL_DEVICE = os.getenv("WHISPER_DEVICE", "auto")
COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "auto")
CORS_ORIGINS = get_csv_env(
    "BACKEND_CORS_ORIGINS",
    ["http://localhost:5173", "http://127.0.0.1:5173"],
)

app = FastAPI(title=APP_TITLE, version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "model_size": get_runtime_model_name(),
        "device": get_runtime_device(),
    }


@app.get("/api/settings/defaults", response_model=SettingsResponse)
def default_settings() -> SettingsResponse:
    return SettingsResponse(
        font_size=36,
        line_height=1.55,
        scroll_speed=28.0,
        content_width=900,
        theme="studio",
        font_family="'IBM Plex Sans KR', sans-serif",
    )


@app.post("/api/progress", response_model=ProgressResponse)
def estimate_progress(payload: ProgressRequest) -> ProgressResponse:
    matched_index, matched_preview, confidence = find_best_match(
        payload.script, payload.recognized_text
    )
    return ProgressResponse(
        matched_index=matched_index,
        matched_preview=matched_preview,
        confidence=confidence,
    )


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)) -> dict[str, object]:
    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
        temp_file.write(await file.read())

    try:
        return transcribe_audio_file(temp_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
