"""
tests/test_db.py
Unit tests for medo/db.py: WAL mode, FTS5 search, upsert, and scrape state.
"""
import pytest
from medo.db import (
    get_connection,
    init_db,
    upsert_question,
    get_question_by_id,
    get_questions_by_topic,
    search_questions,
    upsert_asset,
    get_asset_by_sha1,
    get_scrape_state,
    update_scrape_state,
    set_user_answer,
    toggle_bookmark,
    get_database_stats,
)
from medo.models import AnswerSource, ExplanationStatus, ParsedOption, ParsedQuestion, ParsedImage


@pytest.fixture
def conn(tmp_path):
    db_file = tmp_path / "test_medo.db"
    c = get_connection(db_file)
    init_db(c)
    yield c
    c.close()


def make_dummy_question(idx: int = 1, stem: str = "بیمار دیابتی با درد سینه") -> ParsedQuestion:
    options = [
        ParsedOption(index=1, text="آسپرین", raw_html="<span>آسپرین</span>", vote_percent=10),
        ParsedOption(index=2, text="کلوپیدوگرل", raw_html="<span>کلوپیدوگرل</span>", vote_percent=5),
        ParsedOption(index=3, text="امپاگلیفلوزین", raw_html="<span>امپاگلیفلوزین</span>", vote_percent=80),
        ParsedOption(index=4, text="متفورمین", raw_html="<span>متفورمین</span>", vote_percent=5),
    ]
    q = ParsedQuestion(
        source_url=f"https://medofast.ir/qbank/topic-id-79?page={idx}",
        source_page=idx,
        ordinal=idx,
        stem_html=f"<p>{stem}</p>",
        stem_text=stem,
        options=options,
        correct_index=3,
        answer_source=AnswerSource.MARKER,
        vote_percent=[10, 5, 80, 5],
        explanation_html="<p>امپاگلیفلوزین در نارسایی قلبی و دیابت اندیکاسیون دارد.</p>",
        explanation_text="امپاگلیفلوزین در نارسایی قلبی و دیابت اندیکاسیون دارد.",
        explanation_status=ExplanationStatus.FULL,
        images=[
            ParsedImage(
                original_url="https://s3-strg.medofast.ir/mindmaps/sample.png",
                role="mindmap",
                sha1="abc123sha1",
            )
        ],
        has_images=True,
    )
    q.compute_content_hash()
    return q


def test_init_db(conn):
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}
    assert "questions" in tables
    assert "categories" in tables
    assert "assets" in tables
    assert "question_assets" in tables
    assert "scrape_state" in tables
    assert "questions_fts" in tables


def test_upsert_and_retrieve_question(conn):
    q = make_dummy_question(1)
    qid = upsert_question(conn, q, topic_id=79)
    assert qid > 0

    retrieved = get_question_by_id(conn, qid)
    assert retrieved is not None
    assert retrieved["stem_text"] == q.stem_text
    assert retrieved["correct_index"] == 3
    assert len(retrieved["options"]) == 4
    assert retrieved["options"][2]["text"] == "امپاگلیفلوزین"
    assert retrieved["vote_percent"]["3"] == 80


def test_upsert_deduplication(conn):
    q = make_dummy_question(1)
    qid1 = upsert_question(conn, q, topic_id=79)
    # Upsert same question again
    qid2 = upsert_question(conn, q, topic_id=79)
    assert qid1 == qid2

    stats = get_database_stats(conn)
    assert stats["total_questions"] == 1


def test_fts5_search(conn):
    q1 = make_dummy_question(1, stem="بیمار مبتلا به کتواسیدوز دیابتی")
    q2 = make_dummy_question(2, stem="کودک با تنگی نفس و استریدور")
    upsert_question(conn, q1, topic_id=79)
    upsert_question(conn, q2, topic_id=79)

    # Search Persian term
    results_diab = search_questions(conn, "کتواسیدوز")
    assert len(results_diab) == 1
    assert results_diab[0]["stem_text"] == "بیمار مبتلا به کتواسیدوز دیابتی"

    results_child = search_questions(conn, "استریدور")
    assert len(results_child) == 1
    assert results_child[0]["stem_text"] == "کودک با تنگی نفس و استریدور"


def test_scrape_state(conn):
    update_scrape_state(conn, topic_id=79, last_page=5, total_pages=50, status="running")
    state = get_scrape_state(conn, topic_id=79)
    assert state is not None
    assert state["last_page"] == 5
    assert state["total_pages"] == 50
    assert state["status"] == "running"

    # Update to page 6
    update_scrape_state(conn, topic_id=79, last_page=6, status="completed")
    state = get_scrape_state(conn, topic_id=79)
    assert state["last_page"] == 6
    assert state["status"] == "completed"


def test_asset_management(conn):
    upsert_asset(
        conn,
        sha1="abc123sha1",
        original_url="https://s3-strg.medofast.ir/mindmaps/sample.png",
        local_path="data/assets/abc123sha1.webp",
        mime="image/webp",
        bytes_size=45000,
        role="mindmap",
    )
    asset = get_asset_by_sha1(conn, "abc123sha1")
    assert asset is not None
    assert asset["local_path"] == "data/assets/abc123sha1.webp"
    assert asset["role"] == "mindmap"


def test_user_answer_and_bookmark(conn):
    q = make_dummy_question(1)
    qid = upsert_question(conn, q, topic_id=79)

    set_user_answer(conn, qid, selected_option=3)
    row = get_question_by_id(conn, qid)
    assert row["user_selected_option"] == 3

    is_bookmarked = toggle_bookmark(conn, qid)
    assert is_bookmarked is True
    row = get_question_by_id(conn, qid)
    assert row["bookmarked"] == 1

    is_bookmarked_again = toggle_bookmark(conn, qid)
    assert is_bookmarked_again is False
    row = get_question_by_id(conn, qid)
    assert row["bookmarked"] == 0
