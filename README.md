# Voice Active Prompter

[![Release](https://img.shields.io/github/v/tag/qorbals8165-tech/voice-active-prompter?label=release)](https://github.com/qorbals8165-tech/voice-active-prompter/tags)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-18%2B-339933?logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-1f6feb)](https://github.com/qorbals8165-tech/voice-active-prompter)

Whisper based AI teleprompter that advances scripts from live speech, supports microphone driven auto-progress, keyword jumps, auto-scroll, UI customization, and GPU acceleration when available.

## Features

- Real-time speech transcription with `faster-whisper`
- Transformers-based Korean Whisper support with a Korean-tuned checkpoint
- Native desktop window with microphone selection
- Live microphone listening without audio file upload
- Low-latency chunked recognition with rolling context prompts
- Script sync with recognized speech
- Separate live subtitle output window
- Presentation mode launch for subtitle output
- Mirrored subtitle output for teleprompter glass / reflected setups
- Adobe-inspired AI + microphone desktop app icon
- Keyword jump navigation
- Auto-scroll teleprompter view
- UI customization for font size, line height, theme, and width
- GPU-aware inference mode

## Project Structure

- `backend/`: FastAPI service for transcription and settings
- `frontend/`: React teleprompter client

## Git Sharing Guide (Other PC)

1. Clone the repository.
2. On Windows, run `setup_windows.bat` once for initial setup.
3. (Manual setup only) create local env files from templates:

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

4. Put sensitive values only in `.env` (never commit `.env`).
5. Install backend/frontend dependencies and run.

## Install Screenshots

### Windows setup (`setup_windows.bat`)

![Windows setup screenshot](docs/screenshots/windows-setup.svg)

### Windows desktop run (`run_desktop.bat`)

![Windows desktop run screenshot](docs/screenshots/windows-desktop-run.svg)

## Backend Run

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

The backend runs on `http://localhost:8000`.
Windows에서는 프로젝트 루트에서 `run_backend.bat`로 실행할 수 있습니다.

## Desktop App Run

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.desktop
```

This opens a standalone desktop teleprompter window and a separate live subtitle output window.

Windows에서는 프로젝트 루트에서 `run_desktop.bat`로 실행할 수 있습니다.

## macOS App Icon (Double-click)

```bash
cd "voice active prompter"
./scripts/prepare_macos_app_bundle.sh
```

This syncs `backend` into `Voice Active Prompter.app/Contents/Resources/backend` and sets execute permissions, so launching by double-clicking the app icon is reliable.

## Frontend Run

```bash
cd frontend
npm install
npm run dev
```

The frontend runs on `http://localhost:5173`.
Windows에서는 프로젝트 루트에서 `run_frontend.bat`로 실행할 수 있습니다.

## API Overview

- `GET /api/health`: service health and compute mode
- `POST /api/transcribe`: upload audio and receive recognized text
- `POST /api/progress`: estimate script position from recognized text
- `GET /api/settings/defaults`: load default UI settings

## Notes

- GPU acceleration is enabled automatically when `ctranslate2` detects CUDA support.
- If no GPU is available, the service falls back to CPU.
- `faster-whisper` requires local model download on first run.
- The desktop app listens to the selected system microphone and advances the script inside its own window.
- You can tune response speed, matching sensitivity, and language directly in the desktop app.
- The default transformer backend is `ghost613/whisper-large-v3-turbo-korean`, and you can override it with `TRANSFORMERS_WHISPER_MODEL_ID`.
- `backend/.env` is auto-loaded by the app (model/backend/CORS settings).
- `frontend/.env` can override API endpoint via `VITE_API_BASE_URL`.

## Local Fine-Tuning Recommendation

For a resource-limited local machine such as a MacBook Air, use the lightweight fine-tuning path:

```bash
cd backend
source .venv/bin/activate
python scripts/train_korean_asr.py \
  --model-id openai/whisper-small \
  --dataset-id kresnik/zeroth_korean \
  --max-train-samples 512 \
  --max-eval-samples 64 \
  --max-steps 80 \
  --batch-size 1 \
  --grad-accum 8 \
  --output-dir training_runs/korean_asr_small
```

This configuration is designed to be much more realistic than full large-model fine-tuning on local hardware.
