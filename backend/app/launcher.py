"""uvicorn(백그라운드) + pywebview 창을 띄우는 독립 실행 런처."""

from __future__ import annotations

import logging
import os
import socket
import sys
import tempfile
import threading
import traceback
from pathlib import Path

# 독립 실행(localhost 전용)에서는 API 키를 요구하지 않는다.
os.environ.setdefault("BACKEND_REQUIRE_API_KEY", "false")
os.environ.setdefault("BACKEND_EXPOSE_HEALTH_DETAILS", "true")

_server_ready = threading.Event()
_chosen_port = 7654


def _log_path() -> Path:
    return Path(tempfile.gettempdir()) / "voice-active-prompter.log"


def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("vap")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.FileHandler(_log_path(), mode="w", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


log = _setup_logging()


def _find_free_port(preferred: int) -> int:
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
        except OSError:
            continue
    return preferred


def _start_uvicorn(port: int) -> None:
    try:
        import uvicorn

        from .main import app

        class _ReadyServer(uvicorn.Server):
            async def startup(self, sockets=None):  # type: ignore[override]
                await super().startup(sockets)
                _server_ready.set()
                log.info("uvicorn started on port %s", port)

        # log_config=None: uvicorn이 우리 파일 로깅 핸들러를 덮어쓰지 않도록
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
        _ReadyServer(config).run()
    except Exception:
        log.error("uvicorn thread crashed:\n%s", traceback.format_exc())


def run_webview() -> None:
    global _chosen_port
    log.info("run_webview start; frozen=%s", getattr(sys, "frozen", False))

    import webview  # type: ignore[import]

    _chosen_port = _find_free_port(_chosen_port)
    log.info("chosen port=%s", _chosen_port)

    t = threading.Thread(target=_start_uvicorn, args=(_chosen_port,), daemon=True)
    t.start()

    if not _server_ready.wait(timeout=60):
        log.error("server did not become ready within 60s")

    log.info("creating window")
    webview.create_window(
        "AI PROMPTER",
        f"http://127.0.0.1:{_chosen_port}",
        width=1280,
        height=800,
    )
    webview.start()
    log.info("webview.start() returned (window closed)")


def main() -> None:
    try:
        if "--desktop" in sys.argv:
            from .desktop import run

            run()
        else:
            run_webview()
    except Exception:
        log.error("fatal error in main:\n%s", traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
