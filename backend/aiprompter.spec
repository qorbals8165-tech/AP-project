# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — Voice Active Prompter 독립 실행 패키지.

빌드:  pyinstaller vap.spec --noconfirm
- macOS:  dist/Voice Active Prompter.app
- Windows: dist/VoiceActivePrompter/VoiceActivePrompter.exe

Whisper 모델은 번들에 포함하지 않고 첫 실행 시 HuggingFace에서 캐시로 받는다.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

spec_dir = os.path.abspath(SPECPATH)          # backend/
repo_dir = os.path.dirname(spec_dir)          # voice active prompter/

datas = [
    (os.path.join(repo_dir, "frontend", "dist"), os.path.join("frontend", "dist")),
]
binaries = []
hiddenimports = []

# 동적 임포트가 많은 패키지는 통째로 수집
for pkg in (
    "transformers",
    "librosa",
    "soundfile",
    "sounddevice",
    "sentencepiece",
    "tokenizers",
    "safetensors",
    "huggingface_hub",
    "webview",
):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception as exc:  # noqa: BLE001
        print(f"[vap.spec] collect_all({pkg}) skipped: {exc}")

# uvicorn / 앱 패키지의 하위 모듈
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "app",
    "app.main",
    "app.launcher",
    "app.core",
    "app.desktop",
    "app.document_import",
    "app.native_audio",
    "app.system_fonts",
    "app.paths",
    "app.settings",
    "app.schemas",
    "sounddevice",
    "soundfile",
]

block_cipher = None

_ico_path = os.path.join(spec_dir, "assets", "app_icon.ico")
_exe_icon = _ico_path if os.path.exists(_ico_path) else None

a = Analysis(
    [os.path.join(spec_dir, "run_app.py")],
    pathex=[spec_dir],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"] if sys.platform == "darwin" else [],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="AIPrompter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=_exe_icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="AIPrompter",
)

_icon_path = os.path.join(spec_dir, "assets", "app_icon.icns")
_icon = _icon_path if os.path.exists(_icon_path) else None

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="AI PROMPTER.app",
        icon=_icon,
        bundle_identifier="com.aiprompter.app",
        info_plist={
            "CFBundleName": "AI PROMPTER",
            "CFBundleDisplayName": "AI PROMPTER",
            "NSMicrophoneUsageDescription": "음성 인식을 위해 마이크에 접근합니다.",
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "1.0.0",
        },
    )
