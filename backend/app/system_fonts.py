"""설치된 시스템 폰트의 패밀리명 열거.

외부 의존성 없이 OpenType 'name' 테이블을 직접 파싱한다(.ttf/.otf/.ttc).
브라우저 Local Font Access API가 없는 환경(macOS WKWebView 등)에서도
백엔드가 OS 폰트 폴더를 스캔해 실제 CSS 패밀리명을 돌려준다.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

_FONT_SUFFIXES = {".ttf", ".ttc", ".otf"}


def _font_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library" / "Fonts"]
    if sys.platform.startswith("win"):
        dirs = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
        return dirs
    return [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        home / ".fonts",
        home / ".local" / "share" / "fonts",
    ]


def _decode(raw: bytes, platform_id: int) -> str | None:
    try:
        if platform_id in (0, 3):  # Unicode / Windows → UTF-16BE
            return raw.decode("utf-16-be").replace("\x00", "").strip()
        if platform_id == 1:  # Macintosh → MacRoman
            return raw.decode("mac_roman").strip()
        return raw.decode("utf-16-be", "ignore").replace("\x00", "").strip()
    except Exception:
        return None


def _families_in_sfnt(f, base: int) -> set[str]:
    f.seek(base)
    head = f.read(12)
    if len(head) < 12:
        return set()
    num_tables = struct.unpack(">H", head[4:6])[0]
    table_dir = f.read(16 * num_tables)
    name_off = name_len = None
    for i in range(num_tables):
        rec = table_dir[i * 16 : (i + 1) * 16]
        if len(rec) < 16:
            break
        if rec[0:4] == b"name":
            name_off, name_len = struct.unpack(">II", rec[8:16])
            break
    if name_off is None or not name_len:
        return set()

    f.seek(name_off)
    name_tbl = f.read(name_len)
    if len(name_tbl) < 6:
        return set()
    _fmt, count, string_offset = struct.unpack(">HHH", name_tbl[:6])

    fam16: set[str] = set()  # nameID 16: Typographic Family (우선)
    fam1: set[str] = set()   # nameID 1: Font Family
    for i in range(count):
        rec = name_tbl[6 + i * 12 : 6 + (i + 1) * 12]
        if len(rec) < 12:
            break
        platform_id, _enc, _lang, name_id, length, offset = struct.unpack(">HHHHHH", rec)
        if name_id not in (1, 16):
            continue
        start = string_offset + offset
        raw = name_tbl[start : start + length]
        if len(raw) < length:
            continue
        val = _decode(raw, platform_id)
        if val:
            (fam16 if name_id == 16 else fam1).add(val)
    return fam16 or fam1


def _families_in_file(path: Path) -> set[str]:
    try:
        with open(path, "rb") as f:
            tag = f.read(4)
            if tag == b"ttcf":  # TrueType Collection: 여러 폰트 포함
                f.seek(8)
                num = struct.unpack(">I", f.read(4))[0]
                offsets = struct.unpack(">" + "I" * num, f.read(4 * num))
                out: set[str] = set()
                for off in offsets:
                    out |= _families_in_sfnt(f, off)
                return out
            if tag in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
                return _families_in_sfnt(f, 0)
    except Exception:
        pass
    return set()


# 본문에 쓸 수 없는 폰트(이모지/심볼/점자 등)는 적용해도 글자가 안 바뀌므로 제외
_EXCLUDE_KEYWORDS = ("emoji", "braille", "symbol", "dingbat", "webding", "wingding", "ornament")


def _is_text_font(name: str) -> bool:
    low = name.lower()
    return not any(kw in low for kw in _EXCLUDE_KEYWORDS)


def list_system_fonts() -> list[str]:
    families: set[str] = set()
    for directory in _font_dirs():
        if not directory.is_dir():
            continue
        try:
            for path in directory.rglob("*"):
                if path.suffix.lower() in _FONT_SUFFIXES:
                    families |= _families_in_file(path)
        except Exception:
            continue
    cleaned = {
        fam for fam in families
        if fam and not fam.startswith(".") and fam.isprintable() and _is_text_font(fam)
    }
    return sorted(cleaned, key=str.lower)


if __name__ == "__main__":
    fonts = list_system_fonts()
    print(f"{len(fonts)} families")
    for name in fonts[:40]:
        print(" ", name)
