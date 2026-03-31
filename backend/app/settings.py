from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")


def get_csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name, "")
    if not raw:
        return default
    values = [item.strip() for item in raw.split(",")]
    return [item for item in values if item]
