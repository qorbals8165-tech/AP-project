from __future__ import annotations

import os
import tempfile
import time
from collections import deque
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile, status
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
API_KEY = os.getenv("BACKEND_API_KEY", "").strip()
REQUIRE_API_KEY = os.getenv("BACKEND_REQUIRE_API_KEY", "true").lower() == "true"
RATE_LIMIT_PER_MINUTE = max(int(os.getenv("BACKEND_RATE_LIMIT_PER_MINUTE", "80")), 1)
RATE_LIMIT_MAX_CLIENTS = max(int(os.getenv("BACKEND_RATE_LIMIT_MAX_CLIENTS", "10000")), 100)
MAX_UPLOAD_BYTES = max(int(os.getenv("BACKEND_MAX_UPLOAD_MB", "20")), 1) * 1024 * 1024
EXPOSE_HEALTH_DETAILS = os.getenv("BACKEND_EXPOSE_HEALTH_DETAILS", "false").lower() == "true"
TRUST_PROXY_HEADERS = os.getenv("BACKEND_TRUST_PROXY_HEADERS", "false").lower() == "true"

_rate_limit_lock = Lock()
_request_windows: dict[str, deque[float]] = {}
ALLOWED_AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4"}


def _prune_rate_limit_windows(now: float) -> None:
    stale_clients: list[str] = []
    for ip, entries in _request_windows.items():
        while entries and now - entries[0] > 60.0:
            entries.popleft()
        if not entries:
            stale_clients.append(ip)
    for ip in stale_clients:
        _request_windows.pop(ip, None)

app = FastAPI(title=APP_TITLE, version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def verify_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if not REQUIRE_API_KEY:
        return
    if not API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API key is required but not configured",
        )
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )


def enforce_rate_limit(request: Request) -> None:
    client_ip = request.client.host if request.client and request.client.host else "unknown"
    if TRUST_PROXY_HEADERS:
        xff = request.headers.get("x-forwarded-for", "")
        forwarded = xff.split(",")[0].strip() if xff else ""
        if forwarded:
            client_ip = forwarded
    now = time.monotonic()

    with _rate_limit_lock:
        _prune_rate_limit_windows(now)
        if client_ip not in _request_windows and len(_request_windows) >= RATE_LIMIT_MAX_CLIENTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many distinct clients",
            )
        window = _request_windows.setdefault(client_ip, deque())
        if len(window) >= RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests",
            )
        window.append(now)


@app.get("/api/health")
def health() -> dict[str, str]:
    if EXPOSE_HEALTH_DETAILS:
        return {
            "status": "ok",
            "model_size": get_runtime_model_name(),
            "device": get_runtime_device(),
        }
    return {"status": "ok"}


@app.get("/api/settings/defaults", response_model=SettingsResponse)
def default_settings(
    request: Request,
    _: None = Depends(verify_api_key),
) -> SettingsResponse:
    enforce_rate_limit(request)
    return SettingsResponse(
        font_size=36,
        line_height=1.55,
        scroll_speed=28.0,
        content_width=900,
        theme="studio",
        font_family="'IBM Plex Sans KR', sans-serif",
    )


@app.post("/api/progress", response_model=ProgressResponse)
def estimate_progress(
    payload: ProgressRequest,
    request: Request,
    _: None = Depends(verify_api_key),
) -> ProgressResponse:
    enforce_rate_limit(request)
    matched_index, matched_preview, confidence = find_best_match(
        payload.script, payload.recognized_text
    )
    return ProgressResponse(
        matched_index=matched_index,
        matched_preview=matched_preview,
        confidence=confidence,
    )


@app.post("/api/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    _: None = Depends(verify_api_key),
) -> dict[str, object]:
    enforce_rate_limit(request)

    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    normalized_suffix = suffix.lower()
    content_type = (file.content_type or "").lower()
    if normalized_suffix not in ALLOWED_AUDIO_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported file extension",
        )
    if content_type and not (content_type.startswith("audio/") or content_type in {"application/octet-stream"}):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported media type",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
        total_size = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_BYTES:
                temp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)",
                )
            temp_file.write(chunk)

    try:
        return transcribe_audio_file(temp_path)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Transcription failed") from exc
    finally:
        await file.close()
        temp_path.unlink(missing_ok=True)
