from __future__ import annotations

import hmac
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from threading import Lock

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .core import find_best_match, get_runtime_device, get_runtime_model_name, transcribe_audio_file
from .settings import get_csv_env
from .schemas import ProgressRequest, ProgressResponse, SettingsResponse

ALLOWED_DOC_SUFFIXES = {".txt", ".md", ".docx", ".hwp"}


APP_TITLE = "Voice Active Prompter API"
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
    if x_api_key is None or not hmac.compare_digest(x_api_key, API_KEY):
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
        font_family="system-ui, -apple-system, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Segoe UI', sans-serif",
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
    initial_prompt: str | None = Form(default=None),
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
        return transcribe_audio_file(temp_path, initial_prompt=initial_prompt or None)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Transcription failed") from exc
    finally:
        await file.close()
        temp_path.unlink(missing_ok=True)


@app.get("/api/audio-devices")
def audio_devices(
    request: Request,
    _: None = Depends(verify_api_key),
) -> dict[str, object]:
    enforce_rate_limit(request)
    from .native_audio import list_input_devices

    try:
        return {"devices": list_input_devices()}
    except Exception:
        return {"devices": []}


@app.get("/api/system-fonts")
def system_fonts(
    request: Request,
    _: None = Depends(verify_api_key),
) -> dict[str, object]:
    enforce_rate_limit(request)
    from .system_fonts import list_system_fonts

    try:
        return {"fonts": list_system_fonts()}
    except Exception:
        return {"fonts": []}


@app.post("/api/recognition/start")
async def recognition_start(
    request: Request,
    _: None = Depends(verify_api_key),
) -> dict[str, object]:
    enforce_rate_limit(request)
    from .native_audio import controller

    payload = await request.json()
    device_index = payload.get("device_index")
    transcribe = bool(payload.get("transcribe", True))
    script = str(payload.get("script", ""))
    try:
        idx = int(device_index) if device_index is not None else None
    except (TypeError, ValueError):
        idx = None
    try:
        controller.start(idx, transcribe=transcribe, script=script)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"마이크를 시작할 수 없습니다: {exc}") from exc
    return controller.state()


@app.post("/api/recognition/stop")
def recognition_stop(
    request: Request,
    _: None = Depends(verify_api_key),
) -> dict[str, object]:
    enforce_rate_limit(request)
    from .native_audio import controller

    controller.stop()
    return controller.state()


@app.get("/api/recognition/state")
def recognition_state(
    request: Request,
    _: None = Depends(verify_api_key),
) -> dict[str, object]:
    from .native_audio import controller

    return controller.state()


@app.post("/api/recognition/script")
async def recognition_script(
    request: Request,
    _: None = Depends(verify_api_key),
) -> dict[str, str]:
    from .native_audio import controller

    payload = await request.json()
    controller.set_script(str(payload.get("script", "")))
    return {"status": "ok"}


@app.post("/api/import-document")
async def import_document(
    request: Request,
    file: UploadFile = File(...),
    _: None = Depends(verify_api_key),
) -> dict[str, object]:
    enforce_rate_limit(request)

    suffix = Path(file.filename or "doc.txt").suffix.lower()
    if suffix not in ALLOWED_DOC_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"지원하지 않는 형식입니다. 지원: {', '.join(sorted(ALLOWED_DOC_SUFFIXES))}",
        )

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp_path = Path(tmp.name)
        total_size = 0
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > MAX_UPLOAD_BYTES:
                tmp_path.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"파일이 너무 큽니다 (최대 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB)",
                )
            tmp.write(chunk)

    try:
        from .document_import import extract_text_from_path
        text = extract_text_from_path(tmp_path)
        return {"text": text}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="문서를 처리하지 못했습니다.") from exc
    finally:
        await file.close()
        tmp_path.unlink(missing_ok=True)


# 프로덕션 빌드(frontend/dist)가 있으면 동일 서버에서 정적 파일을 서빙
from .paths import frontend_dist  # noqa: E402

_FRONTEND_DIST = frontend_dist()
if _FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True), name="frontend")
