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
    set_bookmark,
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


def test_fts_index_is_rebuilt_when_it_lags_behind(conn):
    """Rows stored before the FTS triggers existed must still be searchable.

    A full rebuild on every start would be wasteful, so init_db compares the
    row counts and only rebuilds when the index is behind.
    """
    q = make_dummy_question(1, stem="بیمار با سردرد ناگهانی و استفراغ")
    upsert_question(conn, q, topic_id=79)
    assert search_questions(conn, "سردرد")  # triggers keep it in sync

    conn.execute("INSERT INTO questions_fts(questions_fts) VALUES('delete-all')")
    conn.commit()
    assert search_questions(conn, "سردرد") == []

    init_db(conn)  # counts disagree → rebuild
    hits = search_questions(conn, "سردرد")
    assert len(hits) == 1 and "سردرد" in hits[0]["stem_text"]


def test_legacy_fts_schema_is_recreated(conn):
    """The first FTS declaration listed `options_text`, which does not exist in
    `questions`; every content read raised and the index could not be
    rebuilt.  init_db must replace that declaration."""
    import sqlite3

    q = make_dummy_question(1, stem="بیمار با سرگیجه وضعیتی")
    upsert_question(conn, q, topic_id=79)

    for trigger in ("questions_ai", "questions_ad", "questions_au"):
        conn.execute(f"DROP TRIGGER {trigger}")
    conn.execute("DROP TABLE questions_fts")
    conn.execute("""
        CREATE VIRTUAL TABLE questions_fts USING fts5(
            content='questions', content_rowid='id',
            stem_text, explanation_text, options_text
        )
    """)
    conn.execute("""
        INSERT INTO questions_fts(rowid, stem_text, explanation_text, options_text)
        SELECT id, stem_text, coalesce(explanation_text, ''), options_json
        FROM questions
    """)
    conn.commit()

    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT count(*) FROM questions_fts").fetchone()

    init_db(conn)

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'questions_fts'"
    ).fetchone()[0]
    assert "options_text" not in sql and "options_json" in sql
    assert len(search_questions(conn, "سرگیجه")) == 1


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


def test_user_answer_can_be_cleared(conn):
    """«پاسخ مجدد» stores None, so `medo stats` stops counting the attempt."""
    qid = upsert_question(conn, make_dummy_question(1), topic_id=79)
    set_user_answer(conn, qid, selected_option=2)
    assert get_question_by_id(conn, qid)["user_selected_option"] == 2

    set_user_answer(conn, qid, selected_option=None)
    assert get_question_by_id(conn, qid)["user_selected_option"] is None


def test_set_bookmark_is_idempotent(conn):
    qid = upsert_question(conn, make_dummy_question(1), topic_id=79)
    assert set_bookmark(conn, qid, True) is True
    assert set_bookmark(conn, qid, True) is True
    assert get_question_by_id(conn, qid)["bookmarked"] == 1

    assert set_bookmark(conn, qid, False) is False
    assert get_question_by_id(conn, qid)["bookmarked"] == 0


def test_prune_placeholder_nodes(conn):
    """Dead by-year tabs, empty breadcrumb chains and the fallback root go away
    once nothing hangs off them — real nodes stay untouched."""
    from medo.db import (
        FALLBACK_ROOT_SOURCE,
        ensure_fallback_topic_nodes,
        prune_placeholder_nodes,
        upsert_category,
    )

    cat = upsert_category(conn, kind="category", title="پره انترنی",
                          source_url="/qbank/cat-id-2", position=0)
    upsert_category(conn, kind="lesson", title="به تفکیک دوره و سال",
                    source_url="/qbank/cat-id-2/by-year", parent_id=cat)
    real = upsert_category(conn, kind="topic", title="ریه",
                           source_url="/qbank/topic-id-3", parent_id=cat)
    # breadcrumb-only chain: year → lesson → topic, written before discovery
    year = upsert_category(conn, kind="section", title="خرداد 1405",
                           source_url="medo:path:پره انترنی|خرداد 1405",
                           parent_id=cat)
    upsert_category(conn, kind="section", title="ریه",
                    source_url="medo:path:پره انترنی|خرداد 1405|ریه",
                    parent_id=year)

    deleted = prune_placeholder_nodes(conn)
    assert deleted == 3          # by-year leaf + breadcrumb leaf + its parent
    urls = {r["source_url"] for r in conn.execute(
        "SELECT source_url FROM categories").fetchall()}
    assert "/qbank/cat-id-2/by-year" not in urls
    assert "medo:path:پره انترنی|خرداد 1405|ریه" not in urls
    assert "/qbank/topic-id-3" in urls

    # A clean tree is left alone (idempotent).
    assert prune_placeholder_nodes(conn) == 0
    assert {r["source_url"] for r in conn.execute(
        "SELECT source_url FROM categories").fetchall()} == {
            "/qbank/cat-id-2", "/qbank/topic-id-3"}

    # The «دسته‌بندی‌نشده» root disappears once its topics get real parents.
    upsert_category(conn, kind="topic", title="مبحث 79",
                    source_url="/qbank/topic-id-79", parent_id=real)
    ensure_fallback_topic_nodes(conn)
    assert conn.execute("SELECT 1 FROM categories WHERE source_url = ?",
                        (FALLBACK_ROOT_SOURCE,)).fetchone() is None


# ---------------------------------------------------------------------------
# Internet usage accounting
# ---------------------------------------------------------------------------

def test_question_download_bytes_are_stored_and_never_erased(conn):
    q = make_dummy_question(1)
    upsert_question(conn, q, topic_id=79, download_bytes=48_000)
    row = conn.execute(
        "SELECT download_bytes FROM questions WHERE topic_id = 79"
    ).fetchone()
    assert row[0] == 48_000

    # A re-scrape without a measurement must keep the measured value.
    upsert_question(conn, q, topic_id=79, download_bytes=0)
    row = conn.execute(
        "SELECT download_bytes FROM questions WHERE topic_id = 79"
    ).fetchone()
    assert row[0] == 48_000


def test_usage_summary_totals_today_and_average(conn):
    from datetime import datetime, timezone
    from medo.db import get_usage_summary, record_usage

    now = datetime.now(timezone.utc).isoformat()
    record_usage(conn, started_at=now, finished_at=now,
                 byte_count=3_000_000, questions=200, status="completed")
    record_usage(conn, started_at="2020-01-01T00:00:00+00:00",
                 finished_at="2020-01-01T01:00:00+00:00",
                 byte_count=1_000_000, questions=100, status="completed")

    summary = get_usage_summary(conn)
    assert summary["total_bytes"] == 4_000_000
    assert summary["total_questions"] == 300
    assert summary["avg_bytes_per_question"] == 4_000_000 // 300
    assert summary["today_bytes"] == 3_000_000
    assert summary["today_questions"] == 200
    assert summary["runs"] == 2


def test_usage_average_uses_whole_runs_not_question_samples(conn):
    """Real traffic ÷ real questions over complete runs; a single tiny or
    huge question must not define the average."""
    from datetime import datetime, timezone
    from medo.db import get_usage_summary, record_usage

    now = datetime.now(timezone.utc).isoformat()
    record_usage(conn, started_at=now, finished_at=now,
                 byte_count=900_000, questions=3, status="completed")
    record_usage(conn, started_at=now, finished_at=now,
                 byte_count=9_000, questions=1, status="completed")  # excluded
    assert get_usage_summary(conn)["avg_bytes_per_question"] == 300_000


def test_usage_falls_back_to_measured_questions_without_run_history(conn):
    from medo.db import get_usage_summary

    for i, size in enumerate((40_000, 40_960, 60_000), start=1):
        upsert_question(conn, make_dummy_question(i, stem=f"سوال {i}"),
                        topic_id=79, download_bytes=size)
    summary = get_usage_summary(conn)
    assert summary["total_bytes"] == 0
    assert summary["avg_bytes_per_question"] == int((40_000 + 40_960 + 60_000) / 3)


def test_one_measured_question_is_not_enough_for_an_estimate(conn):
    """The first page carries the app shell, so a one-question run is
    reported as unknown (0) instead of becoming a 500 MB projection."""
    from medo.db import get_usage_summary, record_usage

    upsert_question(conn, make_dummy_question(1), topic_id=79,
                    download_bytes=425_347)
    record_usage(conn, started_at="2026-10-24T10:00:00+00:00",
                 finished_at="2026-10-24T10:01:00+00:00",
                 byte_count=425_347, questions=1, status="completed")
    assert get_usage_summary(conn)["avg_bytes_per_question"] == 0


def test_usage_summary_is_zero_until_something_is_measured(conn):
    from medo.db import get_usage_summary

    summary = get_usage_summary(conn)
    assert summary["avg_bytes_per_question"] == 0
    assert summary["today_bytes"] == 0


def test_root_order_is_internship_then_preinternship_then_authored(conn):
    """The user's browse order, independent of the site's position numbers."""
    from medo.db import get_category_tree, upsert_category

    for title, url, pos in [
        ("علوم پایه پزشکی", "/qbank/cat-id-3", 0),
        ("تالیفی بالین", "/qbank/cat-id-6", 1),
        ("پره انترنی", "/qbank/cat-id-1", 2),
        ("دستیاری پزشکی", "/qbank/cat-id-2", 3),
        ("تالیفی علوم پایه", "/qbank/cat-id-7", 4),
    ]:
        upsert_category(conn, kind="category", title=title,
                        source_url=url, position=pos)

    assert [n["title"] for n in get_category_tree(conn)] == [
        "دستیاری پزشکی", "پره انترنی", "تالیفی بالین",
        "تالیفی علوم پایه", "علوم پایه پزشکی",
    ]
