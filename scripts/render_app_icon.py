#!/usr/bin/env python3
"""마스터 아이콘 이미지에서 파생 아이콘(PNG/ICO)을 생성.

마스터 소스: backend/assets/Icon.png (없으면 app_icon_1024.png)
생성물:
  - app_icon_1024.png  (1024² 마스터 — macOS .icns iconset 소스)
  - app_icon.png       (256²  — 데스크톱 창 런타임 아이콘)
  - app_icon.ico       (다중 크기 — Windows 실행 파일 아이콘)

macOS .icns 는 scripts/build_macos_icns.sh 가 app_icon_1024.png 로 생성한다.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ASSETS = Path(__file__).resolve().parents[1] / "backend" / "assets"
MASTER_CANDIDATES = ("Icon.png", "app_icon_1024.png")
ICO_SIZES = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _load_master() -> Image.Image:
    for name in MASTER_CANDIDATES:
        path = ASSETS / name
        if path.exists():
            return Image.open(path).convert("RGBA")
    raise SystemExit(
        f"마스터 아이콘이 없습니다. {ASSETS} 에 "
        f"{' 또는 '.join(MASTER_CANDIDATES)} 를 넣어주세요."
    )


def _resized(img: Image.Image, size: int) -> Image.Image:
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = _load_master()

    _resized(master, 1024).save(ASSETS / "app_icon_1024.png", "PNG")
    _resized(master, 256).save(ASSETS / "app_icon.png", "PNG")
    master.save(ASSETS / "app_icon.ico", sizes=ICO_SIZES)

    print(f"Wrote app_icon_1024.png, app_icon.png, app_icon.ico → {ASSETS}")


if __name__ == "__main__":
    main()
