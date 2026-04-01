# Voice Active Prompter

[![Release](https://img.shields.io/github/v/tag/qorbals8165-tech/voice-active-prompter?label=release)](https://github.com/qorbals8165-tech/voice-active-prompter/tags)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node](https://img.shields.io/badge/node-18%2B-339933?logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-1f6feb)](https://github.com/qorbals8165-tech/voice-active-prompter)

음성을 실시간으로 인식해 대본 진행 위치를 자동으로 맞춰주는 Whisper 기반 AI 텔레프롬프터입니다.  
마이크 입력 기반 자동 진행, 키워드 점프, 자동 스크롤, 자막 출력, UI 커스터마이징을 지원합니다.

## 1) 프로젝트 소개

Voice Active Prompter는 발표자가 마우스 조작 없이 대본을 읽을 수 있게 설계된 데스크톱 중심 프롬프터입니다.

- 실시간 음성 인식 결과를 기준으로 현재 읽는 위치를 추정
- 별도 라이브 자막 출력창 제공
- 프롬프터 반사(미러) 환경을 위한 자막 미러링 지원
- GPU 가능 시 자동 가속, 불가 시 CPU 폴백

## 2) 주요 기능

- `faster-whisper` 기반 실시간 전사
- Transformers 기반 한국어 Whisper 모델 지원 (`ghost613/whisper-large-v3-turbo-korean`)
- 마이크 직접 입력 수집 (파일 업로드 없이 사용 가능)
- 저지연 청크 인식 + 롤링 프롬프트 컨텍스트
- Waveform / Spectrogram / Mel Spectrogram 오디오 디버그 뷰
- 대본 자동 동기화 및 진행 하이라이트
- 발표용 라이브 자막 출력창 / 전체화면 발표 모드
- 미러(좌우 반전) 자막 출력
- 키워드 점프, 자동 스크롤
- 폰트 크기/줄 간격/테마/본문 폭 커스터마이징
- 데스크톱 앱 아이콘 및 단일 실행 경험 강화

## 3) 프로젝트 구조

- `backend/`: FastAPI API + 데스크톱 앱 + 음성 인식 코어
- `frontend/`: React 기반 웹 프롬프터 UI
- `docs/screenshots/`: 설치/실행 스크린샷
- `scripts/`: 아이콘 생성 및 macOS 앱 번들 보조 스크립트

## 4) 설치 방법

### Windows (권장: 자동 설치)

프로젝트 루트에서 1회 실행:

```bat
setup_windows.bat
```

이 스크립트가 다음을 자동으로 처리합니다.

- Python / npm 존재 확인
- `backend/.env`, `frontend/.env` 자동 생성 (`.env.example` 기준)
- `backend` 가상환경 생성 및 `requirements.txt` 설치
- `frontend` `npm install`

추가 템플릿:

- 로컬 개발용: `backend/.env.local.example`
- 배포용: `backend/.env.prod.example`

### macOS / 수동 설치

```bash
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env

cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd ../frontend
npm install
```

## 5) 실행 방법

### Backend API 실행

```bash
cd backend
source .venv/bin/activate
uvicorn app.main:app --reload
```

- 기본 주소: `http://localhost:8000`
- Windows 빠른 실행: `run_backend.bat`

### Desktop 앱 실행

```bash
cd backend
source .venv/bin/activate
python -m app
```

- 독립 실행형 텔레프롬프터 창 + 라이브 자막 창이 함께 열립니다.
- `인식 · 상태` 카드의 `오디오 디버그 창 열기` 버튼으로 입력 진단 창을 띄울 수 있습니다.
- 기본 입력 민감도는 `180%`로 조정되어, 일반 실내 음성에서 게이트 차단이 덜 발생하도록 튜닝되었습니다.
- 인식 프리셋(속도/균형/정확도) 적용 시 입력 민감도도 함께 조정되도록 미세튜닝되었습니다.
- Windows 빠른 실행: `run_desktop.bat`

### Frontend 개발 서버 실행

```bash
cd frontend
npm run dev
```

- 기본 주소: `http://localhost:5173`
- Windows 빠른 실행: `run_frontend.bat`

### macOS 앱 번들(더블클릭 실행용) 준비

```bash
./scripts/prepare_macos_app_bundle.sh
```

## 6) 환경변수

### `backend/.env`

```dotenv
# ASR backend: transformers-whisper | faster-whisper
ASR_BACKEND=transformers-whisper

WHISPER_MODEL_SIZE=distil-large-v3
WHISPER_DEVICE=auto
WHISPER_COMPUTE_TYPE=auto
TRANSFORMERS_WHISPER_MODEL_ID=ghost613/whisper-large-v3-turbo-korean

# Comma-separated frontend origins
BACKEND_CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# Optional API key auth (set non-empty value to require X-API-Key header)
BACKEND_API_KEY=
BACKEND_REQUIRE_API_KEY=false

# Per-IP request budget per minute (in-memory)
BACKEND_RATE_LIMIT_PER_MINUTE=80
BACKEND_RATE_LIMIT_MAX_CLIENTS=10000

# Trust x-forwarded-for for client IP based rate limit (enable only behind trusted proxy)
BACKEND_TRUST_PROXY_HEADERS=false

# Maximum upload payload for /api/transcribe (MB)
BACKEND_MAX_UPLOAD_MB=20

# Expose model/device details on /api/health
BACKEND_EXPOSE_HEALTH_DETAILS=false
```

운영 프로필 권장:

1. 로컬 테스트: `backend/.env.local.example` 기반 (`BACKEND_REQUIRE_API_KEY=false`)
2. 배포 환경: `backend/.env.prod.example` 기반 (`BACKEND_REQUIRE_API_KEY=true`)
3. Windows에서 수동 전환 시:
`copy backend\\.env.local.example backend\\.env`
`copy backend\\.env.prod.example backend\\.env`

### `frontend/.env`

```dotenv
VITE_API_BASE_URL=http://localhost:8000/api
```

## 7) API 개요

- `GET /api/health`: 서비스 상태 (`BACKEND_EXPOSE_HEALTH_DETAILS=true`일 때 모델/디바이스 포함)
- `GET /api/settings/defaults`: UI 기본값 로드
- `POST /api/transcribe`: 오디오 파일 전사
- `POST /api/progress`: 인식 텍스트 기준 대본 진행 위치 추정

배포 보안 권장:

- 공개 배포 시 `BACKEND_API_KEY`를 설정하고 요청 헤더 `X-API-Key`를 사용하세요.
- `BACKEND_REQUIRE_API_KEY=true`를 유지해 인증 누락 배포를 방지하세요.
- `BACKEND_RATE_LIMIT_PER_MINUTE`, `BACKEND_RATE_LIMIT_MAX_CLIENTS`, `BACKEND_MAX_UPLOAD_MB`를 운영 환경에 맞춰 조정하세요.
- 다중 인스턴스/로드밸런서 환경에서는 애플리케이션 내부 limiter만으로는 충분하지 않으므로, WAF/Nginx/Cloud LB 레벨 rate limit를 함께 사용하세요.
- `BACKEND_TRUST_PROXY_HEADERS`는 신뢰 가능한 프록시 뒤에서만 `true`로 설정하세요.
- `BACKEND_EXPOSE_HEALTH_DETAILS=false` 유지(기본값)로 런타임 정보 노출을 최소화하세요.

## 8) 스크린샷 & 데모

### 설치 화면 (`setup_windows.bat`)

![Windows setup screenshot](docs/screenshots/windows-setup.svg)

### 데스크톱 실행 화면 (`run_desktop.bat`)

![Windows desktop run screenshot](docs/screenshots/windows-desktop-run.svg)

### 데모 실행 빠른 가이드

1. `setup_windows.bat` 1회 실행
2. `run_desktop.bat` 실행
3. 마이크 선택 후 대본을 읽으면 자동 진행 확인

## 9) Release Notes (`v1.0.0`)

첫 정식 안정 릴리즈입니다.  
이번 버전은 데스크톱 실사용 흐름과 실시간 음성 인식 정확도/안정성 개선에 초점을 맞췄습니다.

- FastAPI + React + Desktop 통합 실행 흐름 안정화
- 마이크 기반 실시간 전사와 대본 자동 진행 로직 개선
- 발표용 라이브 자막창, 전체화면 모드, 미러(좌우 반전) 모드 제공
- Windows 실행 스크립트(`setup_windows.bat`, `run_*.bat`) 정리
- `.env.example` 기반 초기 설정 절차 표준화
- README 및 스크린샷 문서 보강

## 10) 학습 데이터 진단 (선택)

`train_korean_asr.py` 실행 시 오디오 전처리 점검용 진단 파일을 함께 생성할 수 있습니다.

```bash
cd backend
source .venv/bin/activate
python scripts/train_korean_asr.py \
  --audio-diagnostics \
  --diagnostic-samples-per-split 4 \
  --max-train-samples 512 \
  --max-eval-samples 64
```

생성 위치:

- `training_runs/.../diagnostics/summary.json`
- `training_runs/.../diagnostics/train/sample_XX_spectrogram.png`
- `training_runs/.../diagnostics/train/sample_XX_mel.png`
- `training_runs/.../diagnostics/validation/sample_XX_spectrogram.png`
- `training_runs/.../diagnostics/validation/sample_XX_mel.png`

## 11) 참고

- 첫 실행 시 Whisper 모델 다운로드로 시간이 걸릴 수 있습니다.
- CUDA 감지 시 GPU 가속이 자동 사용됩니다.
- GPU 미탑재 환경에서는 CPU 모드로 자동 전환됩니다.
- 민감정보는 반드시 `.env`에만 두고 Git에 커밋하지 마세요.

## 12) 마이크 단자별 체크리스트 (Windows)

### 1. 3.5mm 마이크 단자 (Mic In, 보통 핑크 / TRS)

1. PC의 `Mic In` 단자에 연결했는지 확인 (헤드폰 출력 단자 X)
2. `설정 > 시스템 > 소리 > 입력`에서 장치가 보이는지 확인
3. 입력 장치 선택 후 말할 때 입력 레벨 반응 확인
4. 앱에서 `새로고침` 후 해당 장치 선택
5. 앱 입력 미터가 움직이지 않으면 Windows 입력 볼륨(게인) 증가

### 2. 3.5mm 헤드셋 콤보 단자 (TRRS)

1. 노트북/PC가 `헤드셋 콤보(TRRS)`를 지원하는지 확인
2. TRS 마이크를 사용할 경우 TRS↔TRRS 변환 젠더 필요 여부 확인
3. Windows에서 “헤드셋 마이크” 입력 장치로 인식되는지 확인
4. 앱에서 해당 장치 선택 후 입력 미터 반응 확인

### 3. 3.5mm 라인 입력 (Line-In, 보통 파랑)

1. `Line-In`은 저레벨 마이크 입력과 감도가 다름을 인지
2. 일반 다이내믹 마이크 직결 시 소리가 매우 작을 수 있음
3. 가능하면 프리앰프/오디오 인터페이스 경유 사용 권장
4. 앱 입력 민감도 상승 + Windows 입력 레벨 조정

### 4. USB 마이크

1. 장치 연결 후 드라이버 자동 설치 완료 확인
2. Windows 기본 입력 장치로 USB 마이크 선택
3. 앱에서 장치 목록 `새로고침` 후 USB 마이크 선택
4. 샘플레이트가 비정상적일 경우 장치 고급 설정에서 16k/48k 확인

### 5. USB 오디오 인터페이스 (XLR/6.35mm 포함)

1. 인터페이스 입력 게인 먼저 조정 (클리핑 LED 확인)
2. 팬텀파워(48V) 필요 마이크는 인터페이스에서 활성화
3. Windows 입력 장치를 오디오 인터페이스로 선택
4. 앱에서 인터페이스 입력 채널 장치 선택
5. 노이즈/지연이 크면 버퍼/샘플레이트 설정 점검

### 6. 블루투스 마이크/헤드셋

1. 연결 프로파일이 `Hands-Free`로 잡히는지 확인
2. 일부 기기에서 음질/지연이 유선 대비 불리할 수 있음
3. 앱 입력 미터가 불안정하면 유선/USB 마이크 우선 권장

### 공통 문제 해결 체크

1. `설정 > 개인정보 및 보안 > 마이크`에서 데스크톱 앱 접근 허용
2. 앱 장치 `새로고침` 후 다시 선택
3. 앱 입력 민감도 조정 (작으면 올리고, 과하면 내리기)
4. 다른 녹음 앱(Windows 음성 녹음기)에서 먼저 입력 동작 확인
