@echo off
REM Windows 배포 패키지 빌드 - dist\AIPrompter\AIPrompter.exe 생성
setlocal

set "DIR=%~dp0"
set "FRONTEND=%DIR%frontend"
set "BACKEND=%DIR%backend"

echo === 1) 백엔드 가상환경 준비 ===
cd /d "%BACKEND%"
if not exist ".venv" (
  python -m venv .venv
)
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo === 2) 프론트엔드 빌드 ===
cd /d "%FRONTEND%"
call npm install
call npm run build

echo === 3) PyInstaller 패키징 ===
cd /d "%BACKEND%"
python -m PyInstaller aiprompter.spec --noconfirm --clean

echo.
echo 완료: %BACKEND%\dist\AIPrompter\AIPrompter.exe
echo 배포 시 dist\AIPrompter 폴더 전체를 압축해 전달하거나
echo Inno Setup 등으로 설치 마법사를 만드세요.
endlocal
pause
