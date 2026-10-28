"""
medo/live_report.py
One pasteable status line-up, built only from counts and log markers.

Debugging a run used to mean asking the owner for screenshots and guessing
between "the site showed another page", "the session ran out" and "the walk
stopped early".  This module answers those from the database and logs/medo.log
without touching a single stem, option or lesson: every value printed here is a
count, an id, a timestamp or a delay.

`medo live-report` prints it; nothing here writes to the database.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from medo.db import (DEFAULT_DB_PATH, count_needing_repair,
                     count_node_questions, declared_questions, get_connection,
                     tag_id_of)

# Log lines that carry a useful number, and nothing paid.  Each pattern only
# extracts digits, so even an unexpected line cannot leak lesson text.
_DRIFT_RE = re.compile(
    r"requested page (\d+),\s*site (?:shows|is at) (\d+)")
_PACE_RE = re.compile(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)s per page")
_STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_TAG_RE = re.compile(r"tag\s+(\d+)")
_PAGE_RE = re.compile(r"page\s+(\d+)")


def _stamp(line: str) -> str:
    match = _STAMP_RE.match(line)
    return match.group(1) if match else "?"


def log_facts(log_path: Path | str) -> dict:
    """Page drift, 429s and the last pace range, extracted by number only."""
    path = Path(log_path)
    facts = {
        "drift": 0, "drift_position": 0, "drift_harvest": 0,
        "last_requested": None, "last_shown": None,
        "throttles": 0, "last_429": None, "last_429_tag": None,
        "pace": None, "pace_event": None,
    }
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return facts
    for line in lines:
        match = _DRIFT_RE.search(line)
        if match:
            facts["drift"] += 1
            facts["drift_position" if "[position]" in line else "drift_harvest"] += 1
            facts["last_requested"] = int(match.group(1))
            facts["last_shown"] = int(match.group(2))
        # A Playwright traceback also contains "line 429"; it is not an event.
        if "429" in line and 'File "' not in line:
            facts["throttles"] += 1
            facts["last_429"] = _stamp(line)
            tag = _TAG_RE.search(line)
            facts["last_429_tag"] = int(tag.group(1)) if tag else None
        pace = _PACE_RE.search(line)
        if pace:
            facts["pace"] = (float(pace.group(1)), float(pace.group(2)))
            facts["pace_event"] = _stamp(line)
    return facts


def db_facts(conn: sqlite3.Connection, worst: int = 5) -> dict:
    """Counts that describe the archive; no content is read out."""
    def one(sql: str, args: tuple = ()) -> int:
        try:
            return int(conn.execute(sql, args).fetchone()[0] or 0)
        except sqlite3.OperationalError:
            return 0

    total = one("SELECT count(*) FROM questions")
    with_nodes = one(
        "SELECT count(DISTINCT question_id) FROM question_nodes")
    with_site_id = one(
        "SELECT count(*) FROM questions WHERE site_question_id IS NOT NULL")

    subtopics: list[dict] = []
    try:
        rows = conn.execute(
            "SELECT source_url FROM categories WHERE source_url LIKE '%?tag=%'"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        tag = tag_id_of(row["source_url"])
        match = re.search(r"topic-id-(\d+)", row["source_url"] or "")
        if tag is None or not match:
            continue
        topic = int(match.group(1))
        subtopics.append({
            "topic": topic, "tag": tag,
            "stored": count_node_questions(conn, topic, tag),
            "declared": declared_questions(conn, topic, tag),
        })
    started = [s for s in subtopics if s["stored"]]
    incomplete = [s for s in started
                  if s["declared"] and s["stored"] < s["declared"]]
    incomplete.sort(key=lambda s: (s["declared"] - s["stored"]), reverse=True)

    return {
        "total": total,
        "with_nodes": min(with_nodes, total),
        "without_nodes": max(total - with_nodes, 0),
        "with_site_id": with_site_id,
        "without_site_id": max(total - with_site_id, 0),
        "repair_queue": count_needing_repair(conn),
        "subtopics": len(subtopics),
        "subtopics_started": len(started),
        "subtopics_done": len(started) - len(incomplete),
        "subtopics_incomplete": len(incomplete),
        "worst": incomplete[:max(1, int(worst))],
        "topics": one("SELECT count(*) FROM questions WHERE topic_id > 0"),
    }


def build_report(
    db_path: Path | str = DEFAULT_DB_PATH,
    log_path: Path | str = "",
    doctor: Optional[dict] = None,
    facts: Optional[dict] = None,
) -> str:
    """The whole summary as plain text, safe to paste into a chat."""
    if not log_path:
        from medo.applog import LOG_FILE

        log_path = LOG_FILE
    logs = log_facts(log_path)
    if facts is None:
        facts = {}
        path = Path(db_path)
        if path.is_file():
            conn = get_connection(path)
            try:
                facts = db_facts(conn)
            finally:
                conn.close()

    lines = [f"MedoOffline live report — {time.strftime('%Y-%m-%d %H:%M')}"]
    verdict = (doctor or {}).get("verdict")
    message = (doctor or {}).get("message")
    if verdict:
        checks = " ".join(
            f"{c.get('name')}={'ok' if c.get('ok') else 'FAIL'}"
            for c in (doctor or {}).get("checks") or [])
        lines.append(f"doctor: {verdict}" + (f" ({checks})" if checks else ""))
        if message:
            lines.append(f"verdict: {message}")
    elif doctor is None:
        lines.append("doctor: not run (network checks skipped)")

    if logs["drift"]:
        lines.append(
            f"pages: {logs['drift']} drift event(s) "
            f"(position {logs['drift_position']}, harvest {logs['drift_harvest']}); "
            f"last requested {logs['last_requested']} shown {logs['last_shown']}")
    else:
        lines.append("pages: no requested/shown drift in the log")

    if facts:
        never = max(facts["subtopics"] - facts["subtopics_started"], 0)
        lines.append(
            f"subtopics: {facts['subtopics']} declared, "
            f"{facts['subtopics_started']} started "
            f"({facts['subtopics_done']} complete, "
            f"{facts['subtopics_incomplete']} incomplete), {never} never downloaded")
        for sub in facts["worst"]:
            lines.append(
                f"  - topic {sub['topic']} tag {sub['tag']}: "
                f"{sub['stored']}/{sub['declared']}")
        lines.append(
            f"questions: {facts['total']} total; nodes {facts['with_nodes']} "
            f"have / {facts['without_nodes']} missing; site id "
            f"{facts['with_site_id']} have / {facts['without_site_id']} missing")
        lines.append(f"repair queue: {facts['repair_queue']}")
    else:
        lines.append("questions: database not found or empty")

    if logs["throttles"]:
        tail = f", last {logs['last_429']}"
        if logs["last_429_tag"]:
            tail += f" (tag {logs['last_429_tag']})"
        lines.append(f"429s: {logs['throttles']} event(s){tail}")
    else:
        lines.append("429s: none in the log")

    if logs["pace"]:
        low, high = logs["pace"]
        lines.append(f"pace: {low:.1f}-{high:.1f} s per page "
                     f"(from log, {logs['pace_event']})")
    else:
        from medo.scraper import DELAY_MAX, DELAY_MIN

        lines.append(f"pace: {DELAY_MIN:.1f}-{DELAY_MAX:.1f} s per page "
                     "(default; no pace line in the log)")
    return "\n".join(lines)
