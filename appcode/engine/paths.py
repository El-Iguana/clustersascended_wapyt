"""Where the app keeps its state. ``CA_DATA_DIR`` overrides (tests use a temp dir)."""
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parents[2] / "data"


def data_dir() -> Path:
    return Path(os.getenv("CA_DATA_DIR", str(_DEFAULT)))
