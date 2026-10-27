"""tests/test_paths_and_log.py
Classification without the site hierarchy (on-page breadcrumb parsing,
fallback topic nodes, ensure_topic_path), the pure login-signal decision,
and the persistent timestamped activity log (medo/applog.py).
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

from medo import applog
from medo.auth import (
    dashboard_url,
    invalidate_session_cache,
    parse_dashboard_navigation,
    session_status,
)
from medo.db import (
    FALLBACK_ROOT_SOURCE,
    FALLBACK_ROOT_TITLE,
    build_topic_paths,
    ensure_fallback_topic_nodes,
    ensure_topic_path,
    get_categories,
    get_connection,
    init_db,
    refresh_question_paths,
    upsert_category,
    upsert_question,
)
from medo.models import AnswerSource, ExplanationStatus, ParsedOption, ParsedQuestion
from medo.scraper import extract_topic_path

FIXTURE = Path(__file__).parent / "fixtures" / "real_before.mhtml"


# ---------------------------------------------------------------------------
# extract_topic_path — the breadcrumb off the question page itself
# ---------------------------------------------------------------------------

FULL_HEADER = (
    '<li><i class="fas fa-books"></i> پره انترنی</li>'
    '<li><i class="fas fa-calendar-check"></i> 1404</li>'
    '<li><i class="fas fa-syringe"></i> ریه</li>'
    '<li><i class="fas fa-hashtag"></i> سل ریوی</li>'
)


def test_extract_topic_path_synthetic_header():
    assert extract_topic_path(FULL_HEADER) == ["پره انترنی", "1404", "ریه", "سل ریوی"]


def test_extract_topic_path_empty_and_topic_only():
    assert extract_topic_path("<p>no path here</p>") == []
    # A lone topic marker without a parent is not a path.
    assert extract_topic_path(
        '<li><i class="fas fa-hashtag"></i> فقط مبحث</li>'
    ) == []


def test_extract_topic_path_stray_shorter_run_does_not_override():
    html = FULL_HEADER + (
        '<li><i class="fas fa-syringe"></i> جراحی</li>'
        '<li><i class="fas fa-hashtag"></i> تروما</li>'
    )
    assert extract_topic_path(html) == ["پره انترنی", "1404", "ریه", "سل ریوی"]


def test_extract_topic_path_strips_invisible_chars():
    html = '<li><i class="fas fa-books"></i> پ\u200bره انترنی</li>' \
           '<li><i class="fas fa-hashtag"></i> مب\u200bحث</li>'
    assert extract_topic_path(html) == ["پره انترنی", "مبحث"]


@pytest.mark.skipif(
    not FIXTURE.exists(), reason="real_before.mhtml not present (scraped content)"
)
def test_extract_topic_path_from_real_page():
    from medo.parser import extract_html_from_mhtml

    html = extract_html_from_mhtml(FIXTURE.read_bytes())
    assert extract_topic_path(html) == [
        "دستیاری پزشکی", "1405", "غدد", "دیابت و هایپوگلایسمی",
    ]


# ---------------------------------------------------------------------------
# DB helpers — fallback grouping + breadcrumb classification
# ---------------------------------------------------------------------------

def _dummy_question(stem: str, page: int = 1) -> ParsedQuestion:
    options = [
        ParsedOption(index=1, text="گزینه یک", raw_html="<span>۱</span>", vote_percent=10),
        ParsedOption(index=2, text="گزینه دو", raw_html="<span>۲</span>", vote_percent=90),
    ]
    q = ParsedQuestion(
        source_url=f"https://medofast.ir/qbank/topic-x?page={page}",
        source_page=page,
        ordinal=page,
        stem_html=f"<p>{stem}</p>",
        stem_text=stem,
        options=options,
        correct_index=2,
        answer_source=AnswerSource.MARKER,
        vote_percent=[10, 90],
        explanation_html="<p>توضیح</p>",
        explanation_text="توضیح",
        explanation_status=ExplanationStatus.FULL,
        images=[],
        has_images=False,
    )
    q.compute_content_hash()
    return q


@pytest.fixture()
def conn(tmp_path):
    c = get_connection(tmp_path / "paths_test.db")
    init_db(c)
    yield c
    c.close()


def test_fallback_nodes_group_uncategorized_topics(conn):
    upsert_question(conn, _dummy_question("سوال مبحث سه"), topic_id=3)
    upsert_question(conn, _dummy_question("سوال مبحث هفتاد و نه"), topic_id=79)

    created = ensure_fallback_topic_nodes(conn)
    assert created == 2
    assert ensure_fallback_topic_nodes(conn) == 0  # idempotent, no dupes

    nodes = get_categories(conn)
    roots = [n for n in nodes if n["source_url"] == FALLBACK_ROOT_SOURCE]
    assert len(roots) == 1 and roots[0]["title"] == FALLBACK_ROOT_TITLE
    children = [n for n in nodes if n["parent_id"] == roots[0]["id"]]
    assert {n["title"] for n in children} == {"مبحث 3", "مبحث 79"}
    assert all(n["kind"] == "topic" for n in children)
    # Real topic ids live in source_url so progress/tree identity works.
    assert {n["source_url"] for n in children} == {
        "/qbank/topic-id-3", "/qbank/topic-id-79",
    }

    # Fallback paths flow into questions too → grouped everywhere (UI, PDF).
    paths = build_topic_paths(conn)
    assert paths["/qbank/topic-id-3"] == f"{FALLBACK_ROOT_TITLE} » مبحث 3"


def test_ensure_topic_path_builds_chain_for_unknown_topic(conn):
    """A topic seen only in a page breadcrumb gets its full chain built."""
    path = ensure_topic_path(
        conn, 77, ["پره انترنی", "1404", "ریه", "سل ریوی"]
    )
    assert path == "پره انترنی » 1404 » ریه » سل ریوی"

    nodes = get_categories(conn)
    topic = next(n for n in nodes if n["source_url"] == "/qbank/topic-id-77")
    assert topic["title"] == "سل ریوی" and topic["kind"] == "topic"

    # Root of the chain: an existing root with the same title is reused
    # instead of creating a duplicate category.
    parents = []
    cur = topic
    while cur.get("parent_id") is not None:
        cur = next(n for n in nodes if n["id"] == cur["parent_id"])
        parents.append(cur)
    assert [p["title"] for p in parents] == ["ریه", "1404", "پره انترنی"]
    assert parents[-1]["source_url"] == "medo:path:پره انترنی"


def test_ensure_topic_path_never_moves_a_topic_that_already_exists(conn):
    """The discovered tree owns the node; breadcrumbs only fix the questions.

    This is the bug behind «بعد از discover سوالات داخل دسته قرار نمی‌گیرند»:
    a breadcrumb pass re-parented a discovered topic into a year-based chain,
    splitting the stored questions away from the category they were filed in.
    """
    root = upsert_category(conn, kind="category", title="پره انترنی",
                           source_url="/qbank/cat-id-2", position=0)
    upsert_category(conn, kind="topic", title="ریه",
                    source_url="/qbank/topic-id-3", parent_id=root)
    upsert_question(conn, _dummy_question("سوال ریه"), topic_id=3)
    refresh_question_paths(conn)

    path = ensure_topic_path(conn, 3, ["پره انترنی", "1404", "ریه", "سل ریوی"])
    assert path == "پره انترنی » ریه"

    nodes = get_categories(conn)
    topic = next(n for n in nodes if n["source_url"] == "/qbank/topic-id-3")
    assert topic["parent_id"] == root
    assert topic["title"] == "ریه"
    assert not any(n["source_url"].startswith("medo:path:") for n in nodes)

    row = conn.execute(
        "SELECT category_path FROM questions WHERE topic_id = 3"
    ).fetchone()
    assert row["category_path"] == "پره انترنی » ریه"


def test_ensure_topic_path_reuses_discovered_root(conn):
    real_root_id = upsert_category(
        conn, kind="category", title="پره انترنی",
        source_url="/qbank/cat-id-2", position=0,
    )
    path = ensure_topic_path(conn, 7, ["پره انترنی", "1404", "قلب", "آریتمی"])
    assert path.startswith("پره انترنی »")

    nodes = get_categories(conn)
    assert not any(n["source_url"] == "medo:path:پره انترنی" for n in nodes)
    topic = next(n for n in nodes if n["source_url"] == "/qbank/topic-id-7")
    # Walk to the top of the chain: the head must be the DISCOVERED root,
    # not a duplicate medo:path: root with the same title.
    top = topic
    while top.get("parent_id") is not None:
        top = next(n for n in nodes if n["id"] == top["parent_id"])
    assert top["id"] == real_root_id
    assert top["source_url"] == "/qbank/cat-id-2"


# ---------------------------------------------------------------------------
# Login proof — the site's dashboard response, never page text
# ---------------------------------------------------------------------------

def test_dashboard_navigation_is_the_only_login_proof():
    # Logged in: Chromium lands on the dashboard itself.
    assert parse_dashboard_navigation(200, "https://medofast.ir/dashboard/welcome")
    assert parse_dashboard_navigation(200, "https://medofast.ir/dashboard/")
    # Guest: the redirect ends on /login?next=/dashboard/welcome — a 200 page
    # that must never be mistaken for the dashboard.
    assert not parse_dashboard_navigation(200, "https://medofast.ir/login?next=/dashboard/welcome")
    assert not parse_dashboard_navigation(302, "https://medofast.ir/dashboard/welcome")
    assert not parse_dashboard_navigation(0, "")


def test_dashboard_url_ignores_query_strings():
    assert dashboard_url("https://medofast.ir/dashboard/welcome")
    # The login shell mentions the dashboard in ?next= — not a login.
    assert not dashboard_url("https://medofast.ir/login?next=/dashboard/welcome")
    assert not dashboard_url("https://medofast.ir/qbank/topic-id-83?page=31")
    assert not dashboard_url("")


def test_session_status_asks_the_site_with_the_saved_cookies(tmp_path, monkeypatch):
    import json

    import medo.auth as auth

    state = tmp_path / "storage_state.json"
    state.write_text(json.dumps({
        "cookies": [{"name": "sessionid", "value": "abc"}],
        "origins": [],
    }), encoding="utf-8")

    seen = {}

    def fake_probe(path, timeout_ms=20000):
        seen["path"] = str(path)
        # Guest session: Chromium follows the redirect to the login shell.
        return False, "https://medofast.ir/login?next=/dashboard/welcome"

    monkeypatch.setattr(auth, "_probe_storage_state", fake_probe)
    invalidate_session_cache(state)
    result = session_status(state)
    assert result["authenticated"] is False
    assert seen["path"] == str(state)
    assert "login" in result["reason"]

    monkeypatch.setattr(
        auth, "_probe_storage_state",
        lambda *a, **k: (True, "https://medofast.ir/dashboard/welcome"),
    )
    invalidate_session_cache(state)
    assert session_status(state)["authenticated"] is True

    # No file at all: never claims a session.
    missing = tmp_path / "absent.json"
    invalidate_session_cache(missing)
    assert session_status(missing)["authenticated"] is False


# ---------------------------------------------------------------------------
# Persistent activity log
# ---------------------------------------------------------------------------

@pytest.fixture()
def log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(applog, "LOG_DIR", tmp_path)
    monkeypatch.setattr(applog, "LOG_FILE", tmp_path / "medo.log")
    return tmp_path / "medo.log"


_TS = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} "


def test_log_writes_timestamped_line(log_file):
    applog.log("hello event")
    content = log_file.read_text(encoding="utf-8")
    assert re.search(_TS + "hello event$", content, re.M)


def test_log_never_raises(tmp_path, monkeypatch):
    # Unwritable location (a path whose parent is a file) must be swallowed.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(applog, "LOG_DIR", blocker / "logs")
    monkeypatch.setattr(applog, "LOG_FILE", blocker / "logs" / "medo.log")
    applog.log("must not blow up")  # no exception = pass


def test_log_rotation(log_file, monkeypatch):
    monkeypatch.setattr(applog, "MAX_BYTES", 120)
    for i in range(10):
        applog.log(f"line {i} " + "x" * 20)
    rotated = log_file.parent / "medo.log.1"
    assert rotated.exists()          # old content was rotated out
    assert log_file.exists()         # current log still writable
    # Bounded: at most MAX_BYTES + one more line before the next rotation.
    assert log_file.stat().st_size <= 120 + 80


def test_tee_buffers_partial_writes(log_file):
    tee = applog._Tee(io.StringIO())
    tee.write("first line")
    tee.write(" second\n")
    tee.write("next\n")
    # Buffered text lands as one line once the newline arrives.
    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert re.search(_TS + "first line second$", lines[0])
    assert re.search(_TS + "next$", lines[1])


def test_controller_push_feeds_ui_and_file(log_file):
    from medo.server import SCRAPER_CONTROLLER

    SCRAPER_CONTROLLER.logs = []
    SCRAPER_CONTROLLER._push("پیام آزمایشی")
    assert SCRAPER_CONTROLLER.logs == ["پیام آزمایشی"]
    assert "[ui] پیام آزمایشی" in log_file.read_text(encoding="utf-8")

    for i in range(70):
        SCRAPER_CONTROLLER._push(f"msg {i}")
    assert len(SCRAPER_CONTROLLER.logs) <= 60  # cap enforced by _push
    SCRAPER_CONTROLLER.logs = []


# ---------------------------------------------------------------------------
# Post-login continuation — the interrupted download restarts by itself
# ---------------------------------------------------------------------------

def test_resume_after_login_replays_only_an_auth_stop(monkeypatch, tmp_path):
    from medo.server import ScraperTaskController

    controller = ScraperTaskController()
    # Isolated archive: a resume request stored in the developer's real
    # database must never decide this unit test.
    controller.db_path = tmp_path / "isolated.db"
    from medo.db import get_connection as _get_conn, init_db as _init_db
    _conn = _get_conn(controller.db_path)
    _init_db(_conn)
    _conn.close()
    calls = {}

    def fake_start(**kwargs):
        calls.update(kwargs)
        return True, "شروع شد"

    monkeypatch.setattr(controller, "start", fake_start)
    # Keep the unit test out of the real logs/medo.log.
    monkeypatch.setattr(controller, "_push", lambda msg: None)

    # Nothing ran yet: no continuation.
    ok, _ = controller.resume_after_login()
    assert ok is False and calls == {}

    controller.last_request = {
        "topic_id": 83, "start_page": 12, "max_pages": None,
        "db_path": tmp_path / "medo.db", "resume": False, "topic_ids": [83, 79],
    }

    # A different failure must not be retried automatically.
    controller.status = "error"
    assert controller.resume_after_login()[0] is False
    assert calls == {}

    controller.status = "auth_required"
    ok, msg = controller.resume_after_login()
    assert ok is True and msg == "شروع شد"
    # Same download, always resuming (never restart page 1 by accident).
    assert calls["topic_id"] == 83
    assert calls["topic_ids"] == [83, 79]
    assert calls["resume"] is True
    assert calls["start_page"] is None

    # While something is already running, login must not start a second run —
    # but it must not be lost either: the running worker continues it itself
    # as its last step (otherwise a login that lands a second too early just
    # downloads nothing).
    calls.clear()
    controller.is_running = True
    ok, msg = controller.resume_after_login()
    assert ok is True and "ادامه" in msg
    assert calls == {}
    assert controller._resume_pending is True


def test_resume_after_login_also_continues_a_guest_quota_stop(monkeypatch, tmp_path):
    """The guest daily allowance stops a run with `limit`, not `auth_required`.

    The same URL leads to /login and a confirmed login lifts the limit, so
    that run must continue too instead of asking the user to start over.
    """
    from medo.server import ScraperTaskController

    controller = ScraperTaskController()
    controller.db_path = tmp_path / "isolated.db"
    calls = {}

    def fake_start(**kwargs):
        calls.update(kwargs)
        return True, "شروع شد"

    monkeypatch.setattr(controller, "start", fake_start)
    monkeypatch.setattr(controller, "_push", lambda msg: None)

    controller.last_request = {
        "topic_id": 83, "start_page": None, "max_pages": None,
        "db_path": tmp_path / "medo.db", "resume": True, "topic_ids": [83],
    }
    controller.status = "limit"

    assert controller.resume_after_login()[0] is True
    assert calls["topic_id"] == 83 and calls["resume"] is True
