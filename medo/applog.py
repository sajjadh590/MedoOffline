"""
medo/applog.py
Durable, timestamped activity log for MedoOffline.

Operationally-important events (controller messages, harvester stdout,
login results, tree-discovery summaries and errors) used to live only in
the web UI's in-memory log or in launcher.log (raw, un-timestamped stdout)
— so after any session, figuring out what happened meant asking the user.
This module appends every event to one file (logs/medo.log) with local
timestamps, and can tee stdout/stderr into it so plain ``print()`` output
from any thread is captured too.

Guarantees: logging must never crash the app — every failure here is
swallowed silently. Writes are line-buffered and serialized by a lock.
The file rotates by size (keeps a couple of older copies).
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "medo.log"
MAX_BYTES = 1_000_000       # rotate shortly after 1 MB
KEEP_ROTATED = 2            # medo.log.1 … medo.log.2

_lock = threading.Lock()
_tee_installed = False


def log(message: str) -> None:
    """Append one timestamped UTF-8 line. Never raises."""
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp} {str(message).rstrip()}\n"
        with _lock:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            _rotate_if_needed()
            with open(LOG_FILE, "a", encoding="utf-8") as fp:
                fp.write(line)
    except Exception:
        pass


def _rotate_if_needed() -> None:
    """Called with the lock held. Keep the log bounded on disk."""
    try:
        if not (LOG_FILE.is_file() and LOG_FILE.stat().st_size > MAX_BYTES):
            return
        oldest = LOG_DIR / f"medo.log.{KEEP_ROTATED}"
        if oldest.exists():
            oldest.unlink()
        for i in range(KEEP_ROTATED - 1, 0, -1):
            src = LOG_DIR / f"medo.log.{i}"
            if src.exists():
                src.rename(LOG_DIR / f"medo.log.{i + 1}")
        LOG_FILE.rename(LOG_DIR / "medo.log.1")
    except Exception:
        pass


class _Tee:
    """Line-buffered duplicate of a text stream into the persistent log.

    ``print()`` may write a message and the trailing newline as separate
    calls; buffering until a newline keeps one logical message on one
    timestamped log line.
    """

    def __init__(self, original):
        self._original = original
        self._buf = ""

    def write(self, data):
        try:
            result = self._original.write(data)
        except Exception:
            result = None
        try:
            self._buf += data
            while "\n" in self._buf:
                line, _, self._buf = self._buf.partition("\n")
                line = line.rstrip("\r")
                if line.strip():
                    log(line)
            # Safety net for huge chunks written without newlines.
            if len(self._buf) > 4096:
                line, self._buf = self._buf, ""
                if line.strip():
                    log(line)
        except Exception:
            pass
        return result

    def flush(self):
        try:
            return self._original.flush()
        except Exception:
            return None

    def __getattr__(self, name):
        # isatty(), fileno(), encoding, writelines(), … delegate untouched.
        return getattr(self._original, name)


def install_tee() -> bool:
    """Route sys.stdout/sys.stderr prints into medo.log. Idempotent."""
    global _tee_installed
    if _tee_installed:
        return False
    with _lock:
        if _tee_installed:
            return False
        try:
            if not isinstance(sys.stdout, _Tee):
                sys.stdout = _Tee(sys.stdout)
            if not isinstance(sys.stderr, _Tee):
                sys.stderr = _Tee(sys.stderr)
            _tee_installed = True
        except Exception:
            return False
    return True
