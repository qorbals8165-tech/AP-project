@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "BACKEND_DIR=%SCRIPT_DIR%backend"

if not exist "%BACKEND_DIR%" (
  echo backend 폴더를 찾을 수 없습니다: "%BACKEND_DIR%"
  pause
  exit /b 1
)

cd /d "%BACKEND_DIR%"

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m app
  goto :end
)

where py >nul 2>&1
if %errorlevel%==0 (
  py -m app
  goto :end
)

where python >nul 2>&1
if %errorlevel%==0 (
  python -m app
  goto :end
)

echo Python을 찾을 수 없습니다.
echo backend\.venv 생성 후 requirements 설치가 필요합니다.
echo 예시:
echo   cd backend
echo   py -m venv .venv
echo   .venv\Scripts\pip install -r requirements.txt
pause
exit /b 1

:end
endlocal
