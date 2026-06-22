"""PyInstaller 진입점 — 패키징된 실행 파일이 이 스크립트를 실행한다."""

import multiprocessing

from app.launcher import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
