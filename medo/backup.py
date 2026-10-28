"""
medo/backup.py
One consistent copy of the archive before a repair can rewrite rows.

Repairs and tag backfills overwrite stored answers and lessons.  A bug (or a
page served in a different state) would then destroy the only copy of text the
account can still show.  The copy is made with SQLite's own backup API, so a
running WAL is included, and only the newest few are kept.

It runs once per process: a session that repairs three subtopics makes one
copy, not three.  `scripts/backfill_tags.py` calls it directly; the local
server calls it before starting a repair job.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

from medo.paths import DEFAULT_DB_PATH

BACKUP_DIRNAME = "backups"
KEEP_BACKUPS = 5

# One copy per process (per database).  Tests reset it explicitly.
_done: set[str] = set()


def reset_backup_guard() -> None:
    """Forget the once-per-session rule (tests, or after the DB was replaced)."""
    _done.clear()


def backup_database(
    db_path: Path | str = DEFAULT_DB_PATH,
    keep: int = KEEP_BACKUPS,
) -> Optional[Path]:
    """Copy `db_path` to `<dir>/backups/<name>-YYYYMMDD-HHMM.db`.

    Returns the copy's path, or None when there is nothing to do (missing DB,
    already copied in this process, or the copy failed).  Never raises: a
    backup problem must not be the thing that stops a repair.
    """
    path = Path(db_path)
    if not path.is_file():
        return None
    key = str(path.resolve())
    if key in _done:
        return None

    out_dir = path.parent / BACKUP_DIRNAME
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{path.stem}-{time.strftime('%Y%m%d-%H%M')}.db"
        if not target.exists():
            source = sqlite3.connect(str(path))
            try:
                dest = sqlite3.connect(str(target))
                try:
                    source.backup(dest)
                finally:
                    dest.close()
            finally:
                source.close()
        _prune(out_dir, path.stem, keep)
    except (OSError, sqlite3.Error):
        return None
    _done.add(key)
    return target


def _prune(out_dir: Path, stem: str, keep: int) -> None:
    """Keep the newest `keep` copies; a half-written copy is not a backup."""
    files = sorted(out_dir.glob(f"{stem}-*.db"), reverse=True)
    for old in files[max(1, int(keep)):]:
        try:
            old.unlink()
        except OSError:
            pass
