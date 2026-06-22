#!/usr/bin/env bash
# macOS 배포 패키지 빌드 — dist/Voice Active Prompter.app 생성
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND="$DIR/frontend"
BACKEND="$DIR/backend"
PY="$BACKEND/.venv/bin/python3"

echo "▶ 프론트엔드 빌드…"
cd "$FRONTEND"
npm install
npm run build

echo "▶ PyInstaller 패키징…"
cd "$BACKEND"
"$PY" -m PyInstaller aiprompter.spec --noconfirm --clean

echo "✓ 완료: $BACKEND/dist/AI PROMPTER.app"
echo "  배포 시 이 .app 을 압축하거나 .dmg 로 만들어 전달하세요."
