"""tests/test_server_api.py

End-to-end checks of the local HTTP server: the parts a malformed or hostile
request reaches before any scraper code runs.

* ``/assets/`` must never serve a file outside the assets directory.
* A request addressed to a foreign Host name (DNS rebinding) must be refused.
* Bad JSON / bad numbers must answer with a JSON error instead of killing the
  handler thread and dropping the connection.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import threading
import time

import pytest

from medo.db import get_connection, init_db
from medo.paths import DEFAULT_DB_PATH
from medo.server import (
    BACKFILL_INTERVAL_SECONDS,
    MedoRequestHandler,
    ScraperTaskController,
    _backfill_due,
    _backfill_stamp_path,
)


@pytest.fixture()
def live_server(tmp_path):
    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()

    handler = lambda *a, **kw: MedoRequestHandler(*a, db_path=db, **kw)  # noqa: E731
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], db
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(port: int, method: str, path: str, body: bytes | None = None,
             host: str | None = None, content_type: str = "application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    skip_host = host is not None
    conn.putrequest(method, path, skip_host=skip_host)
    if skip_host:
        conn.putheader("Host", host)
    if body is not None:
        conn.putheader("Content-Type", content_type)
        conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(body)
    resp = conn.getresponse()
    payload = resp.read()
    conn.close()
    return resp.status, payload


def _post_json(port: int, path: str, payload) -> dict:
    status, raw = _request(port, "POST", path, json.dumps(payload).encode())
    assert status == 200, raw[:300]
    return json.loads(raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------

def test_assets_traversal_is_refused(live_server):
    port, _ = live_server
    status, body = _request(port, "GET", "/assets/../../medo/server.py")
    assert status == 404, body[:200]


def test_assets_unknown_file_is_404(live_server):
    port, _ = live_server
    status, _ = _request(port, "GET", "/assets/nope.webp")
    assert status == 404


# ---------------------------------------------------------------------------
# Host guard (DNS rebinding)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/stats", None),
    ("POST", "/api/bookmark", b'{"question_id": 1}'),
])
def test_foreign_host_is_rejected(live_server, method, path, body):
    port, _ = live_server
    status, _ = _request(port, method, path, body=body, host="evil.example.com")
    assert status == 403


def test_loopback_host_is_accepted(live_server):
    port, _ = live_server
    status, _ = _request(port, "GET", "/api/stats", host=f"127.0.0.1:{port}")
    assert status == 200


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

def _seed_question(db) -> int:
    conn = get_connection(db)
    cur = conn.execute(
        """INSERT INTO questions (topic_id, source_url, source_page, ordinal,
               content_hash, stem_html, stem_text, options_json,
               answer_source, explanation_status)
           VALUES (83, 'https://medofast.ir/qbank/topic-id-83', 1, 1,
                   'hash-1', '<p>سوال</p>', 'سوال',
                   '[{"index": 1, "text": "الف"}, {"index": 2, "text": "ب"}]',
                   'unknown', 'unknown')"""
    )
    conn.commit()
    qid = int(cur.lastrowid)
    conn.close()
    return qid


def test_answer_is_persisted(live_server):
    port, db = live_server
    qid = _seed_question(db)
    assert _post_json(port, "/api/answer",
                      {"question_id": qid, "selected_option": 2})["ok"] is True
    conn = get_connection(db)
    value = conn.execute("SELECT user_selected_option FROM questions WHERE id = ?",
                         (qid,)).fetchone()[0]
    conn.close()
    assert value == 2


def _stored_answer(db, qid):
    conn = get_connection(db)
    value = conn.execute("SELECT user_selected_option FROM questions WHERE id = ?",
                         (qid,)).fetchone()[0]
    conn.close()
    return value


def test_answer_can_be_cleared(live_server):
    """«پاسخ مجدد» must also clear the archive copy, not only localStorage."""
    port, db = live_server
    qid = _seed_question(db)
    _post_json(port, "/api/answer", {"question_id": qid, "selected_option": 1})
    assert _stored_answer(db, qid) == 1

    assert _post_json(port, "/api/answer",
                      {"question_id": qid, "selected_option": 0})["ok"] is True
    assert _stored_answer(db, qid) is None


def test_answer_outside_the_question_options_is_refused(live_server):
    port, db = live_server
    qid = _seed_question(db)          # two options only
    reply = _post_json(port, "/api/answer",
                       {"question_id": qid, "selected_option": 9})
    assert reply["ok"] is False
    assert _stored_answer(db, qid) is None


def test_answer_for_unknown_question_is_refused(live_server):
    port, _ = live_server
    reply = _post_json(port, "/api/answer",
                       {"question_id": 99999, "selected_option": 1})
    assert reply["ok"] is False


def test_bookmark_sets_the_state_it_is_given(live_server):
    """Explicit state, not a toggle: a retried request must not flip back."""
    port, db = live_server
    qid = _seed_question(db)
    assert _post_json(port, "/api/bookmark",
                      {"question_id": qid, "bookmarked": True})["bookmarked"] is True
    assert _post_json(port, "/api/bookmark",
                      {"question_id": qid, "bookmarked": True})["bookmarked"] is True
    assert _post_json(port, "/api/bookmark",
                      {"question_id": qid, "bookmarked": False})["bookmarked"] is False
    conn = get_connection(db)
    flag = conn.execute("SELECT bookmarked FROM questions WHERE id = ?",
                        (qid,)).fetchone()[0]
    conn.close()
    assert flag == 0


def test_bad_answer_ids_answer_with_json_and_keep_the_server_alive(live_server):
    port, _ = live_server
    reply = _post_json(port, "/api/answer", {"question_id": "abc"})
    assert reply["ok"] is False and reply["message"]
    # The connection was not dropped: the very next call still works.
    assert _post_json(port, "/api/bookmark", {"question_id": "abc"})["ok"] is False


def test_invalid_download_parameters_do_not_start_a_run(live_server):
    port, _ = live_server
    from medo.server import SCRAPER_CONTROLLER

    reply = _post_json(port, "/api/scrape/start",
                       {"topic_id": 83, "start_page": "not-a-number"})
    assert reply["ok"] is False
    assert "نامعتبر" in reply["message"]
    assert SCRAPER_CONTROLLER.is_running is False


def test_malformed_json_body_is_ignored_not_fatal(live_server):
    port, _ = live_server
    status, raw = _request(port, "POST", "/api/bookmark", b"{not json")
    assert status == 200
    assert json.loads(raw.decode("utf-8"))["ok"] is False


# ---------------------------------------------------------------------------
# Startup path backfill must not spend the guest allowance every launch
# ---------------------------------------------------------------------------

def test_status_log_sequence_survives_the_sliding_window(monkeypatch):
    """The UI gets the last 25 lines, so it needs an absolute counter to know
    how many are new; len(logs) stops changing once the window is full."""
    import medo.server as srv

    monkeypatch.setattr(srv.applog, "log", lambda *a, **k: None)
    controller = ScraperTaskController()
    for i in range(40):
        controller._push(f"line {i}")

    status = controller.get_status()
    assert len(status["logs"]) == 25
    assert status["logs"][-1] == "line 39"
    assert status["logs_seq"] == 40


def test_backfill_is_due_only_once_a_day(tmp_path):
    db = tmp_path / "medo.db"
    db.write_bytes(b"")
    assert _backfill_due(db) is True          # never attempted

    stamp = _backfill_stamp_path(db)
    stamp.write_text("just ran\n", encoding="utf-8")
    assert _backfill_due(db) is False         # fresh stamp

    old = time.time() - BACKFILL_INTERVAL_SECONDS - 60
    os.utime(stamp, (old, old))
    assert _backfill_due(db) is True          # older than the interval


def test_default_paths_are_absolute():
    assert DEFAULT_DB_PATH.is_absolute()
    assert DEFAULT_DB_PATH.name == "medo.db"


def test_db_growth_uses_the_runs_own_database(tmp_path):
    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()

    controller = ScraperTaskController()
    controller.db_path = db
    controller.stored_total_before = 0
    assert controller._db_grew() is False

    _seed_question(db)
    assert controller._db_grew() is True


# ---------------------------------------------------------------------------
# Internet usage in the live status payload
# ---------------------------------------------------------------------------

def test_status_reports_unmeasured_usage_as_zero(tmp_path):
    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()

    controller = ScraperTaskController()
    controller.db_path = db
    usage = controller.get_status()["usage"]
    assert usage["run_bytes"] == 0
    assert usage["avg_question_bytes"] == 0
    assert usage["estimated_bytes"] == 0
    assert usage["measured"] is False


def test_status_estimates_the_remaining_download_from_measured_average(tmp_path):
    """200 questions × measured average, using the site's declared count minus
    what is already stored — the number the UI shows before/while downloading."""
    from datetime import datetime, timezone
    from medo.db import record_usage, save_run_request

    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.execute(
        "INSERT INTO categories (kind, title, source_url, question_count) "
        "VALUES ('topic', 'ریه', '/qbank/topic-id-83', 20)"
    )
    for i in range(4):
        conn.execute(
            "INSERT INTO questions (topic_id, source_url, source_page, ordinal,"
            " content_hash, stem_html, stem_text, options_json, answer_source,"
            " explanation_status) VALUES (83, 'u', ?, ?, ?, 's', 's', '[]',"
            " 'unknown', 'unknown')",
            (i + 1, i + 1, f"h-{i}"),
        )
    save_run_request(conn, {"topic_id": 83, "targets": [{"topic_id": 83}],
                            "max_pages": None, "db_path": str(db)})
    now = datetime.now(timezone.utc).isoformat()
    record_usage(conn, started_at=now, finished_at=now,
                 byte_count=2_000_000, questions=100, status="completed")
    conn.close()

    controller = ScraperTaskController()
    controller.db_path = db
    usage = controller.get_status()["usage"]
    assert usage["avg_question_bytes"] == 20_000
    assert usage["estimated_questions"] == 16          # 20 declared − 4 stored
    assert usage["estimated_bytes"] == 16 * 20_000
    assert usage["measured"] is True


# ---------------------------------------------------------------------------
# Subtopic-first downloads
#
# A whole-topic pass stored every question with tag NULL, so the sidebar's
# subtopics stayed empty until a separate backfill ran.  When the tree knows
# the subtopics, the controller must queue those instead.
# ---------------------------------------------------------------------------

def test_expand_subtopics_queues_the_tree_children(tmp_path):
    from medo.db import upsert_category

    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    upsert_category(conn, kind="topic", source_url="/qbank/topic-id-15",
                    title="قلب", position=1, question_count=53)
    upsert_category(conn, kind="subtopic",
                    source_url="/qbank/topic-id-15?tag=2778",
                    title="سندروم حاد کرونری", position=2, question_count=30)
    upsert_category(conn, kind="subtopic",
                    source_url="/qbank/topic-id-15?tag=2790",
                    title="آریتمی", position=3, question_count=23)
    conn.close()

    wanted = [{"topic_id": 15, "tag_id": None, "subtopic_title": None}]
    expanded = ScraperTaskController._expand_subtopics(db, wanted)

    assert [t["tag_id"] for t in expanded] == [2778, 2790]
    assert expanded[0]["subtopic_title"] == "سندروم حاد کرونری"
    assert all(t["topic_id"] == 15 for t in expanded)


def test_expand_subtopics_keeps_a_topic_without_children(tmp_path):
    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()

    wanted = [{"topic_id": 99, "tag_id": None, "subtopic_title": None}]
    assert ScraperTaskController._expand_subtopics(db, wanted) == wanted


def test_expand_subtopics_never_touches_an_explicit_tag(tmp_path):
    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()

    wanted = [{"topic_id": 15, "tag_id": 2778, "subtopic_title": "x"}]
    assert ScraperTaskController._expand_subtopics(db, wanted) == wanted


def test_truncated_subtopic_is_sent_back_to_page_one(tmp_path):
    """A subtopic recorded complete with 1 of 7 questions must re-download."""
    from medo.db import get_target_state, update_micro_state

    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    update_micro_state(conn, 15, 2778, last_page=40, status="completed")
    conn.close()

    controller = ScraperTaskController.__new__(ScraperTaskController)
    controller.db_path = db
    controller._reset_truncated_target(15, 2778)

    conn = get_connection(db)
    state = get_target_state(conn, 15, 2778)
    conn.close()
    assert state["last_page"] == 0
    assert state["status"] == "paused"
