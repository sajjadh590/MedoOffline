"""
medo/jobs.py
Durable progress for long, stop-able jobs (the «پخش سوال‌ها در زیرمبحث‌ها»
pass and, later, any other batch).

A job used to exist only as a thread-local status string: the UI showed
«running» with no counters, a page reload forgot everything, and the only way
to stop it was to close the app.  The record lives in SQLite instead:

* ``state`` — running / stopping / stopped / completed / error / throttled,
* ``processed`` / ``total`` — pages (or items) done and known,
* ``current`` — the topic/subtopic and page being read,
* ``logs`` — the last 20 lines for the live panel,
* ``last_progress_at`` — a watchdog compares it with now; > 60 s without
  progress is shown as a stalled job, with the last log line.

The row is written by the worker (and by ``scripts/backfill_tags.py`` through
``--job-id``), never by the HTTP request thread, so a slow page cannot freeze
the UI.  The browser polls ``/api/scrape/status`` and receives the same record
from the database, which is also why a reload changes nothing.
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

KEEP_LOGS = 20
STALL_SECONDS = 60
ACTIVE_STATES = {"running", "stopping"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def create_job(conn: sqlite3.Connection, kind: str, total: int = 0,
               current: str = "") -> int:
    cur = conn.execute(
        "INSERT INTO jobs(kind, state, total, processed, current, logs, "
        "last_log, last_progress_at) VALUES (?, 'running', ?, 0, ?, '[]', '', ?)",
        (str(kind), int(total), str(current or ""), _now_iso()),
    )
    conn.commit()
    return int(cur.lastrowid)


def update_job(conn: sqlite3.Connection, job_id: int, **fields) -> None:
    """Update whitelisted fields; progress columns also reset the watchdog."""
    allowed = {"state", "total", "processed", "current", "last_log", "error",
               "stop_requested", "logs"}
    values = {k: v for k, v in fields.items() if k in allowed}
    if not values:
        return
    progress = bool({"total", "processed", "current"} & set(values))
    sets = [f"{key} = ?" for key in values]
    args = list(values.values())
    sets.append("updated_at = CURRENT_TIMESTAMP")
    if progress:
        sets.append("last_progress_at = ?")
        args.append(_now_iso())
    args.append(int(job_id))
    conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
    conn.commit()


def add_log(conn: sqlite3.Connection, job_id: int, line: str,
            keep: int = KEEP_LOGS) -> None:
    text = str(line or "").strip()
    if not text:
        return
    row = conn.execute("SELECT logs FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
    try:
        lines = json.loads(row["logs"] or "[]") if row else []
    except (TypeError, ValueError):
        lines = []
    lines.append(text)
    update_job(conn, job_id, logs=json.dumps(lines[-keep:], ensure_ascii=False),
               last_log=text)


def request_stop(conn: sqlite3.Connection, job_id: int) -> None:
    update_job(conn, job_id, stop_requested=1)


def stop_requested(conn: sqlite3.Connection, job_id: Optional[int]) -> bool:
    if not job_id:
        return False
    try:
        row = conn.execute(
            "SELECT stop_requested, state FROM jobs WHERE id = ?",
            (int(job_id),)).fetchone()
        return bool(row and (row["stop_requested"] or row["state"] == "stopping"))
    except sqlite3.OperationalError:
        return False


def get_job(conn: sqlite3.Connection, job_id: Optional[int]) -> Optional[dict]:
    if not job_id:
        return None
    try:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?",
                           (int(job_id),)).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    job = dict(row)
    try:
        job["logs"] = json.loads(job.get("logs") or "[]")
    except (TypeError, ValueError):
        job["logs"] = []
    return _decorate(job)


def latest_job(conn: sqlite3.Connection, kind: Optional[str] = None) -> Optional[dict]:
    try:
        if kind:
            row = conn.execute(
                "SELECT * FROM jobs WHERE kind = ? ORDER BY id DESC LIMIT 1",
                (str(kind),)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    job = dict(row)
    try:
        job["logs"] = json.loads(job.get("logs") or "[]")
    except (TypeError, ValueError):
        job["logs"] = []
    return _decorate(job)


def _decorate(job: dict) -> dict:
    """Add derived counters the UI/CLI must not compute differently."""
    started = _parse(job.get("started_at"))
    progress_at = _parse(job.get("last_progress_at")) or started
    now = datetime.now(timezone.utc)
    elapsed = max((now - started).total_seconds(), 0.0) if started else 0.0
    since = max((now - progress_at).total_seconds(), 0.0) if progress_at else 0.0
    processed = int(job.get("processed") or 0)
    total = int(job.get("total") or 0)
    per_min = (processed / (elapsed / 60.0)) if elapsed >= 5 and processed else 0.0
    remaining = max(total - processed, 0) if total else 0
    eta = int((remaining / per_min) * 60) if per_min > 0 and remaining else None
    job.update({
        "processed": processed,
        "total": total,
        "items_per_minute": round(per_min, 1),
        "eta_seconds": eta,
        "seconds_since_progress": int(since),
        "stalled": bool(job.get("state") in ACTIVE_STATES
                        and since >= STALL_SECONDS),
        "progress_percent": (round(100.0 * processed / total, 1)
                             if total else None),
    })
    return job


def _parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
