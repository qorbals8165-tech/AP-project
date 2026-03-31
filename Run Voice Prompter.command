#!/bin/bash
# Finder에서 Documents 제한 없이 터미널로 실행합니다 (더블클릭).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/backend"
if [[ -x .venv/bin/python3 ]]; then
  exec .venv/bin/python3 -m app
fi
echo "backend/.venv 이 없습니다. 터미널에서: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
read -r -p "Enter 키를 누르면 닫습니다…"
