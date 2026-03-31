#!/usr/bin/env bash
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$DIR/backend"
cd "$BACKEND"

VENV_PY="$BACKEND/.venv/bin/python3"
if [[ -x "$VENV_PY" ]]; then
  exec "$VENV_PY" -m app
fi

if command -v python3 >/dev/null 2>&1; then
  exec python3 -m app
fi
if command -v python >/dev/null 2>&1; then
  exec python -m app
fi
echo "Python을 찾을 수 없습니다. backend/.venv 를 만들고 pip install -r requirements.txt 를 실행하세요." >&2
exit 1
