"""
medo/paths.py
One source of truth for on-disk locations.

Every default used to be relative to the *current working directory*
(``Path("data/medo.db")``).  Launched from a shortcut, a task scheduler or
another shell, that silently pointed at a different (usually empty) data
directory: the app looked as if the whole archive had vanished and every
setting was forgotten.  All defaults are now anchored to the project root;
callers may still pass their own path (tests use tmp dirs).
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DATA_DIR / "medo.db"
DEFAULT_STORAGE_STATE = DATA_DIR / "storage_state.json"
DEFAULT_ASSETS_DIR = DATA_DIR / "assets"
DIST_DIR = PROJECT_ROOT / "dist"
LOG_DIR = PROJECT_ROOT / "logs"
