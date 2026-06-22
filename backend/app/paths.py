"""번들/개발 환경 모두에서 정적 리소스 경로를 해석."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def resource_root() -> Path:
    """프론트엔드 dist 등 번들 데이터의 루트.

    - PyInstaller: 임시 추출 폴더(sys._MEIPASS)
    - 개발 모드: 저장소 루트(voice active prompter/)
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS"))
    # backend/app/paths.py → backend/app → backend → (repo root)
    return Path(__file__).resolve().parent.parent.parent


def frontend_dist() -> Path:
    return resource_root() / "frontend" / "dist"
