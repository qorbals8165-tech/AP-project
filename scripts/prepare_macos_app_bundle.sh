#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP="$ROOT/Voice Active Prompter.app"
LAUNCHER="$APP/Contents/MacOS/launcher"
RESOURCES="$APP/Contents/Resources"
BACKEND_SRC="$ROOT/backend/"
BACKEND_DST="$RESOURCES/backend"

if [[ ! -d "$APP" ]]; then
  echo "앱 번들을 찾을 수 없습니다: $APP" >&2
  exit 1
fi

mkdir -p "$RESOURCES"

rsync -a --delete \
  --exclude "__pycache__/" \
  --exclude "*.pyc" \
  --exclude ".DS_Store" \
  "$BACKEND_SRC" "$BACKEND_DST"

chmod +x "$LAUNCHER" "$ROOT/Run Voice Prompter.command" "$ROOT/run_desktop.command"

if [[ -x "$BACKEND_DST/.venv/bin/python3" ]]; then
  echo "번들 준비 완료: backend + .venv 포함"
else
  echo "번들 준비 완료: backend 복사됨 (.venv 미포함, 실행 전 설치 필요)"
fi
