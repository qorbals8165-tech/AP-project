#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PY="$ROOT/backend/.venv/bin/python3"
if [[ -x "$PY" ]]; then
  "$PY" "$ROOT/scripts/render_app_icon.py"
else
  python3 "$ROOT/scripts/render_app_icon.py"
fi

SRC="$ROOT/backend/assets/app_icon_1024.png"
ICONSET="$ROOT/AppIcon.iconset"
rm -rf "$ICONSET"
mkdir -p "$ICONSET"

sips -z 16 16 "$SRC" --out "$ICONSET/icon_16x16.png" >/dev/null
sips -z 32 32 "$SRC" --out "$ICONSET/icon_16x16@2x.png" >/dev/null
sips -z 32 32 "$SRC" --out "$ICONSET/icon_32x32.png" >/dev/null
sips -z 64 64 "$SRC" --out "$ICONSET/icon_32x32@2x.png" >/dev/null
sips -z 128 128 "$SRC" --out "$ICONSET/icon_128x128.png" >/dev/null
sips -z 256 256 "$SRC" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
sips -z 256 256 "$SRC" --out "$ICONSET/icon_256x256.png" >/dev/null
sips -z 512 512 "$SRC" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
sips -z 512 512 "$SRC" --out "$ICONSET/icon_512x512.png" >/dev/null
sips -z 1024 1024 "$SRC" --out "$ICONSET/icon_512x512@2x.png" >/dev/null

APP_RESOURCES="$ROOT/Voice Active Prompter.app/Contents/Resources"
mkdir -p "$APP_RESOURCES"
iconutil -c icns "$ICONSET" -o "$APP_RESOURCES/AppIcon.icns"
rm -rf "$ICONSET"
echo "Created $APP_RESOURCES/AppIcon.icns"
