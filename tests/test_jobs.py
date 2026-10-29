"""tests/test_jobs.py — durable progress for the subtopic organizer.

A fake slow worker proves three behaviours the UI depends on: progress is
visible while it runs, Stop takes effect at the next item, and a re-run can
continue from what is already committed.  No browser and no site.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from medo import jobs
from medo.db import get_connection, init_db


def _db(tmp_path):
    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    return conn


def test_progress_counts_percent_rate_and_eta(tmp_path):
    conn = _db(tmp_path)
    job_id = jobs.create_job(conn, "tags", total=100, current="topic 78")
    jobs.update_job(conn, job_id, processed=20, current="topic 78 tag 1470 page 3")
    # Backdate the start so the per-minute rate has a stable denominator.
    conn.execute("UPDATE jobs SET started_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
                  job_id))
    conn.commit()
    job = jobs.get_job(conn, job_id)
    conn.close()

    assert job["processed"] == 20 and job["total"] == 100
    assert job["progress_percent"] == 20.0
    assert job["items_per_minute"] > 0
    assert job["eta_seconds"] is not None
    assert job["current"].endswith("page 3")
    assert job["stalled"] is False


def test_sixty_seconds_without_progress_is_flagged(tmp_path):
    conn = _db(tmp_path)
    job_id = jobs.create_job(conn, "tags", total=10)
    jobs.add_log(conn, job_id, "topic 78 page 4: matched")
    conn.execute("UPDATE jobs SET last_progress_at = ? WHERE id = ?",
                 ((datetime.now(timezone.utc) - timedelta(seconds=90)).isoformat(),
                  job_id))
    conn.commit()
    job = jobs.get_job(conn, job_id)
    conn.close()
    assert job["stalled"] is True
    assert job["seconds_since_progress"] >= 60
    assert job["last_log"] == "topic 78 page 4: matched"


def test_logs_keep_the_last_twenty_lines(tmp_path):
    conn = _db(tmp_path)
    job_id = jobs.create_job(conn, "tags")
    for i in range(30):
        jobs.add_log(conn, job_id, f"page {i}")
    job = jobs.get_job(conn, job_id)
    conn.close()
    assert len(job["logs"]) == 20
    assert job["logs"][-1] == "page 29"


def test_stop_is_seen_by_a_running_worker_within_a_second(tmp_path):
    db = tmp_path / "medo.db"
    conn = _db(tmp_path)
    job_id = jobs.create_job(conn, "tags", total=50)
    conn.close()

    def worker():
        local = get_connection(db)
        try:
            for i in range(50):
                time.sleep(0.05)
                if jobs.stop_requested(local, job_id):
                    jobs.update_job(local, job_id, state="stopped",
                                    current="stopped at page boundary")
                    return
                jobs.update_job(local, job_id, processed=i + 1)
        finally:
            local.close()

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        check = get_connection(db)
        try:
            job = jobs.get_job(check, job_id)
        finally:
            check.close()
        if job["processed"] >= 3:
            break
        time.sleep(0.02)
    assert job["processed"] >= 3

    conn = get_connection(db)
    jobs.request_stop(conn, job_id)
    conn.close()
    thread.join(timeout=2)
    assert not thread.is_alive()          # stopped within seconds, not at 50

    conn = get_connection(db)
    job = jobs.get_job(conn, job_id)
    conn.close()
    assert job["state"] == "stopped"
    assert job["processed"] < 50


def test_a_stopped_job_can_resume_from_its_saved_count(tmp_path):
    conn = _db(tmp_path)
    job_id = jobs.create_job(conn, "tags", total=40)
    jobs.update_job(conn, job_id, processed=12, state="stopped")
    jobs.request_stop(conn, job_id)
    assert jobs.stop_requested(conn, job_id)

    # A new run opens a fresh job but starts at the cursor the script saved;
    # this record must still say where the old one stopped.
    old = jobs.get_job(conn, job_id)
    assert old["processed"] == 12
    assert old["progress_percent"] == 30.0
    conn.close()
