#!/usr/bin/env python3
"""1024px 앱 아이콘 PNG 생성 (Voice Active Prompter)."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024


def render_icon() -> Image.Image:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rr = int(SIZE * 0.224)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=rr, fill=(22, 24, 30, 255))

    # 오렌지 그라데이션 느낌 (동심원)
    cx, cy = SIZE // 2, int(SIZE * 0.42)
    for i in range(12):
        r = int(SIZE * (0.32 - i * 0.015))
        t = i / 11.0
        r_col = int(240 - t * 40)
        g_col = int(110 - t * 30)
        b_col = int(45 - t * 15)
        a = 255 - i * 10
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(r_col, g_col, b_col, min(a, 255)))

    # 텔레프롬프터 줄
    left = int(SIZE * 0.18)
    right = int(SIZE * 0.82)
    gap = int(SIZE * 0.055)
    y0 = int(SIZE * 0.58)
    bar_h = int(SIZE * 0.045)
    for i in range(4):
        y = y0 + i * gap
        w = right - left - i * int(SIZE * 0.04)
        draw.rounded_rectangle(
            (left, y, left + w, y + bar_h),
            radius=bar_h // 2,
            fill=(245, 242, 236, 235),
        )

    # 마이크 실루엣 (하단)
    mx = SIZE // 2
    my = int(SIZE * 0.28)
    mw = int(SIZE * 0.12)
    mh = int(SIZE * 0.16)
    draw.rounded_rectangle((mx - mw // 2, my - mh, mx + mw // 2, my + mh // 3), radius=mw // 2, fill=(18, 20, 26, 255))
    stem_w = int(SIZE * 0.028)
    stem_h = int(SIZE * 0.06)
    draw.rounded_rectangle(
        (mx - stem_w // 2, my + mh // 3, mx + stem_w // 2, my + mh // 3 + stem_h),
        radius=stem_w // 2,
        fill=(18, 20, 26, 255),
    )

    return img


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "backend" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    im = render_icon()
    master = assets / "app_icon_1024.png"
    im.save(master, "PNG")
    small = im.resize((256, 256), Image.Resampling.LANCZOS)
    small.save(assets / "app_icon.png", "PNG")
    print(f"Wrote {master} and {assets / 'app_icon.png'}")


if __name__ == "__main__":
    main()
