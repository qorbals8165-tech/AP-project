@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "FRONTEND_DIR=%SCRIPT_DIR%frontend"

if not exist "%FRONTEND_DIR%" (
  echo frontend 폴더를 찾을 수 없습니다: "%FRONTEND_DIR%"
  pause
  exit /b 1
)

cd /d "%FRONTEND_DIR%"

if not exist ".env" if exist ".env.example" (
  copy /y ".env.example" ".env" >nul
)

where npm >nul 2>&1
if not %errorlevel%==0 (
  echo npm을 찾을 수 없습니다. Node.js 설치가 필요합니다.
  pause
  exit /b 1
)

if not exist "node_modules" (
  call npm install
  if not %errorlevel%==0 (
    echo npm install 실패
    pause
    exit /b 1
  )
)

call npm run dev
if not %errorlevel%==0 (
  echo npm run dev 실패
  pause
  exit /b 1
)

endlocal
