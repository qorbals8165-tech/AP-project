@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%backend"
set "FRONTEND_DIR=%SCRIPT_DIR%frontend"

echo [1/7] 프로젝트 폴더 확인 중...
if not exist "%BACKEND_DIR%" (
  echo backend 폴더를 찾을 수 없습니다: "%BACKEND_DIR%"
  pause
  exit /b 1
)
if not exist "%FRONTEND_DIR%" (
  echo frontend 폴더를 찾을 수 없습니다: "%FRONTEND_DIR%"
  pause
  exit /b 1
)

echo [2/7] Python 확인 중...
set "PY_CMD="
where py >nul 2>&1
if %errorlevel%==0 set "PY_CMD=py"
if not defined PY_CMD (
  where python >nul 2>&1
  if %errorlevel%==0 set "PY_CMD=python"
)
if not defined PY_CMD (
  echo Python을 찾을 수 없습니다. Python 3 설치 후 다시 실행해주세요.
  pause
  exit /b 1
)

echo [3/7] Node/npm 확인 중...
where npm >nul 2>&1
if not %errorlevel%==0 (
  echo npm을 찾을 수 없습니다. Node.js 설치 후 다시 실행해주세요.
  pause
  exit /b 1
)

echo [4/7] backend 환경파일 준비 중...
if not exist "%BACKEND_DIR%\.env" if exist "%BACKEND_DIR%\.env.example" (
  copy /y "%BACKEND_DIR%\.env.example" "%BACKEND_DIR%\.env" >nul
)

echo [5/7] backend 가상환경/의존성 설치 중...
cd /d "%BACKEND_DIR%"
if not exist ".venv\Scripts\python.exe" (
  call %PY_CMD% -m venv .venv
  if not %errorlevel%==0 (
    echo backend .venv 생성 실패
    pause
    exit /b 1
  )
)
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if not %errorlevel%==0 (
  echo pip 업그레이드 실패
  pause
  exit /b 1
)
call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
if not %errorlevel%==0 (
  echo backend requirements 설치 실패
  pause
  exit /b 1
)

echo [6/7] frontend 환경파일/의존성 준비 중...
cd /d "%FRONTEND_DIR%"
if not exist ".env" if exist ".env.example" (
  copy /y ".env.example" ".env" >nul
)
call npm install
if not %errorlevel%==0 (
  echo frontend npm install 실패
  pause
  exit /b 1
)

echo [7/7] 설치 완료
echo.
echo 이제 아래 배치 파일로 실행할 수 있습니다:
echo   run_backend.bat
echo   run_frontend.bat
echo   run_desktop.bat
echo.
pause
endlocal
