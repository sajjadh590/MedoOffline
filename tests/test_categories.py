"""tests/test_categories.py

Subtopic («?tag=») categories and restart-proof continuation of a download.

Two bugs these lock down:

* every downloaded question landed in one bucket, because a subtopic shared
  its parent topic's row (and its cursor) and no per-subtopic attribution was
  stored at all;
* the post-login continuation lived only in the server process, so a restart
  (or a login that completed a second too late) silently lost the rest of the
  download.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from medo.db import (
    clear_run_request,
    get_category_progress,
    get_connection,
    get_micro_state,
    get_target_state,
    init_db,
    load_run_request,
    normalize_category_kinds,
    pending_resume_targets,
    save_run_request,
    update_micro_state,
    update_scrape_state,
    upsert_category,
    upsert_question,
)
from medo.models import AnswerSource, ExplanationStatus, ParsedOption, ParsedQuestion
from medo.scraper import (
    Harvester,
    read_current_page,
    read_question_position,
    topic_url,
)


def _question(stem: str, page: int = 1) -> ParsedQuestion:
    options = [
        ParsedOption(index=1, text="گزینه یک", raw_html="<span>۱</span>", vote_percent=10),
        ParsedOption(index=2, text="گزینه دو", raw_html="<span>۲</span>", vote_percent=90),
    ]
    q = ParsedQuestion(
        source_url="https://medofast.ir/qbank/topic-x",
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
    )
    q.compute_content_hash()
    return q


@pytest.fixture()
def conn(tmp_path):
    c = get_connection(tmp_path / "cat.db")
    init_db(c)
    yield c
    c.close()


# ---------------------------------------------------------------------------
# Subtopic URL + cursor
# ---------------------------------------------------------------------------

def test_topic_url_keeps_tag_and_page_in_the_right_shape():
    assert topic_url(94, tag_id=1781).endswith("/qbank/topic-id-94?tag=1781")
    assert topic_url(94, page=3, tag_id=1781).endswith("?tag=1781&page=3")
    # A plain topic keeps its old, tested form.
    assert topic_url(79, page=3).endswith("?page=3")
    assert topic_url(79) == "https://medofast.ir/qbank/topic-id-79"


def test_question_position_comes_from_the_counter_not_a_stale_next_button():
    """The micro-topic widget renders several duplicate next buttons.

    Reading the first one's data-page reported «page 14» while the page itself
    said 2, so a resume stopped with «topic shorter than resume target» after
    one question.  The «سوال N از M» counter is the only honest marker.
    """
    html = (
        '<div class="q-header">سوال ۲ از ۳۹</div>'
        '<button data-nav-role="next" data-page="14">بعدی</button>'
        '<button data-nav-role="next" data-page="3">بعدی</button>'
    )
    assert read_question_position("سوال ۲ از ۳۹") == (2, 39)
    assert read_question_position("سوال 12 از 341") == (12, 341)
    assert read_question_position("صفحه دیگری") == (None, None)
    assert read_current_page(html) == 2
    # Without a counter the old marker still works (single valid button).
    assert read_current_page(
        '<button data-nav-role="next" data-page="7">' ) == 6


def test_subtopic_cursor_is_separate_from_the_topic_cursor(conn, tmp_path):
    update_scrape_state(conn, topic_id=94, last_page=12, status="running")
    update_micro_state(conn, topic_id=94, tag_id=1781, last_page=3,
                       total_pages=5, status="paused")

    assert get_micro_state(conn, 94, 1781)["last_page"] == 3
    assert get_target_state(conn, 94, 1781)["status"] == "paused"
    assert get_target_state(conn, 94)["last_page"] == 12
    # Writing one never touches the other (that was the «ھمه یکجا» bug).
    update_micro_state(conn, topic_id=94, tag_id=1781, last_page=4,
                       status="running")
    assert get_target_state(conn, 94)["last_page"] == 12


def test_harvester_writes_the_cursor_of_a_subtopic(conn, tmp_path):
    h = Harvester(db_path=tmp_path / "cat.db")
    h._write_state(conn, 94, 1781, 7, "running")
    assert get_micro_state(conn, 94, 1781)["last_page"] == 7
    # The topic cursor must stay untouched by a subtopic run.
    assert get_target_state(conn, 94) is None


# ---------------------------------------------------------------------------
# Per-question attribution
# ---------------------------------------------------------------------------

def test_question_keeps_its_subtopic_when_re_scraped_as_a_topic(conn):
    q = _question("سوال نفرولوژی")
    upsert_question(conn, q, topic_id=94, tag_id=1781,
                    subtopic_title="نفرولوژی و اورولوژی", category_path="اطفال")

    row = conn.execute("SELECT * FROM questions").fetchone()
    assert row["tag_id"] == 1781
    assert row["subtopic_title"] == "نفرولوژی و اورولوژی"

    # A later whole-topic run must not erase the finer classification.
    upsert_question(conn, q, topic_id=94, category_path="اطفال")
    row = conn.execute("SELECT * FROM questions").fetchone()
    assert row["tag_id"] == 1781
    assert row["subtopic_title"] == "نفرولوژی و اورولوژی"


def test_progress_counts_a_subtopic_on_its_own_row(conn):
    upsert_category(conn, kind="subtopic", title="نفرولوژی و اورولوژی",
                    source_url="/qbank/topic-id-94?tag=1781",
                    parent_id=upsert_category(
                        conn, kind="topic", title="اطفال",
                        source_url="/qbank/topic-id-94"),
                    question_count=39)
    upsert_question(conn, _question("الف"), topic_id=94, tag_id=1781,
                    subtopic_title="نفرولوژی و اورولوژی")
    upsert_question(conn, _question("ب"), topic_id=94, tag_id=1781,
                    subtopic_title="نفرولوژی و اورولوژی")
    upsert_question(conn, _question("ج"), topic_id=94)
    update_micro_state(conn, topic_id=94, tag_id=1781, last_page=1,
                       status="paused")

    progress = get_category_progress(conn)
    sub = progress["/qbank/topic-id-94?tag=1781"]
    topic = progress["/qbank/topic-id-94"]
    assert sub["scraped"] == 2 and sub["declared"] == 39
    assert sub["status"] == "paused"
    # The topic row still counts every stored question of the topic.
    assert topic["scraped"] == 3


def test_node_kinds_follow_the_url_not_the_crawl_depth(conn):
    cat = upsert_category(conn, kind="category", title="دستیاری پزشکی",
                          source_url="/qbank/cat-id-4")
    topic = upsert_category(conn, kind="section", title="اطفال",
                            source_url="/qbank/topic-id-94", parent_id=cat)
    sub = upsert_category(conn, kind="section", title="نفرولوژی و اورولوژی",
                          source_url="/qbank/topic-id-94?tag=1781",
                          parent_id=topic)
    chain = upsert_category(conn, kind="section", title="پره انترنی",
                            source_url="medo:path:پره انترنی")

    normalize_category_kinds(conn)
    kinds = {
        r["id"]: r["kind"] for r in conn.execute(
            "SELECT id, kind FROM categories").fetchall()
    }
    assert kinds[cat] == "category"
    assert kinds[topic] == "topic"
    assert kinds[sub] == "subtopic"
    assert kinds[chain] == "category"


# ---------------------------------------------------------------------------
# Restart-proof continuation
# ---------------------------------------------------------------------------

def test_pending_targets_include_subtopics_in_tree_order(conn):
    cat = upsert_category(conn, kind="category", title="دستیاری پزشکی",
                          source_url="/qbank/cat-id-4", position=1)
    topic = upsert_category(conn, kind="topic", title="اطفال",
                            source_url="/qbank/topic-id-94", parent_id=cat,
                            position=0)
    upsert_category(conn, kind="subtopic", title="نفرولوژی و اورولوژی",
                    source_url="/qbank/topic-id-94?tag=1781",
                    parent_id=topic, position=0)
    update_scrape_state(conn, topic_id=83, last_page=30, status="limit")
    update_micro_state(conn, topic_id=94, tag_id=1781, last_page=2,
                       status="auth_required")
    update_scrape_state(conn, topic_id=79, last_page=5, status="completed")

    pending = pending_resume_targets(conn)
    keys = [(p["topic_id"], p["tag_id"]) for p in pending]
    assert (94, 1781) in keys and (83, None) in keys
    assert (79, None) not in keys          # finished work is not pending
    assert next(p for p in pending if p["tag_id"] == 1781)["label"] == \
        "نفرولوژی و اورولوژی"


def test_run_request_survives_and_clears(conn):
    save_run_request(conn, {"targets": [{"topic_id": 94, "tag_id": 1781}],
                            "max_pages": None})
    assert load_run_request(conn)["targets"][0]["tag_id"] == 1781
    clear_run_request(conn)
    assert load_run_request(conn) is None


def test_controller_normalizes_duplicate_subtopic_targets(monkeypatch):
    from medo.server import ScraperTaskController

    targets = ScraperTaskController._normalize_targets(
        94, None, 1781, "نفرولوژی و اورولوژی",
        [{"topic_id": 94, "tag_id": 1781,
          "subtopic_title": "نفرولوژی و اورولوژی"},
         {"topic_id": 94, "tag_id": 1781},
         {"topic_id": 94}],
    )
    assert len(targets) == 2
    assert targets[0]["tag_id"] == 1781
    assert targets[1]["tag_id"] is None


def test_login_resume_after_restart_uses_the_stored_request(tmp_path, monkeypatch):
    """The server restarted between the stop and the login — continue anyway."""
    from medo.server import ScraperTaskController

    db = tmp_path / "cat.db"
    c = get_connection(db)
    init_db(c)
    save_run_request(c, {
        "topic_id": 79,
        "targets": [{"topic_id": 79}, {"topic_id": 83}],
        "max_pages": None,
        "db_path": str(db),
    })
    update_scrape_state(c, topic_id=79, last_page=12, status="completed")
    update_scrape_state(c, topic_id=83, last_page=30, status="limit")
    c.close()

    controller = ScraperTaskController()
    controller.db_path = db
    controller.status = "idle"          # fresh process: nothing in memory
    calls = {}

    def fake_start(**kw):
        calls.update(kw)
        return True, "شروع شد"

    monkeypatch.setattr(controller, "_push", lambda msg: None)
    monkeypatch.setattr(controller, "start", fake_start)

    ok, _ = controller.resume_after_login()
    assert ok is True
    # Only the unfinished topic is re-opened; the finished one is skipped.
    assert [t["topic_id"] for t in calls["targets"]] == [83]
    assert calls["resume"] is True and calls["start_page"] is None


def test_manual_resume_replays_a_paused_download(tmp_path, monkeypatch):
    from medo.server import ScraperTaskController

    db = tmp_path / "cat.db"
    c = get_connection(db)
    init_db(c)
    save_run_request(c, {"topic_id": 94,
                         "targets": [{"topic_id": 94, "tag_id": 1781,
                                      "subtopic_title": "نفرولوژی"}],
                         "max_pages": 5})
    update_micro_state(c, topic_id=94, tag_id=1781, last_page=3,
                       status="paused")
    c.close()

    controller = ScraperTaskController()
    controller.db_path = db
    monkeypatch.setattr(controller, "_push", lambda msg: None)
    calls = {}

    def fake_start(**kw):
        calls.update(kw)
        return True, "ادامه"

    monkeypatch.setattr(controller, "start", fake_start)

    ok, _ = controller.resume_pending()
    assert ok is True
    assert calls["targets"][0]["tag_id"] == 1781
    assert calls["max_pages"] == 5


def test_login_resume_ignores_a_non_login_failure(tmp_path, monkeypatch):
    """`blocked`/`error` are not login problems and must not auto-retry."""
    from medo.server import ScraperTaskController

    db = tmp_path / "cat.db"
    c = get_connection(db)
    init_db(c)
    save_run_request(c, {"targets": [{"topic_id": 79}]})
    c.close()

    controller = ScraperTaskController()
    controller.db_path = db
    controller.status = "blocked"
    monkeypatch.setattr(controller, "_push", lambda msg: None)
    monkeypatch.setattr(controller, "start", lambda **kw: (True, "x"))
    assert controller.resume_after_login()[0] is False
