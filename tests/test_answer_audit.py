"""tests/test_answer_audit.py — the unanswered-question finder.

Only the pure parts run here: the browser pass needs the live site, so it is
verified by hand. The rules that matter (which rows are picked, and how a
question is recognised on its page) are covered without a network.
"""
from medo.answer_audit import missing_questions, normalize_options
from medo.db import get_connection, init_db, upsert_question
from medo.models import (AnswerSource, ExplanationStatus, ParsedOption,
                         ParsedQuestion)


def _question(stem: str, answer: int | None) -> ParsedQuestion:
    options = [ParsedOption(index=i, text=f"گزینه {i}", raw_html="", vote_percent=None)
               for i in range(1, 5)]
    q = ParsedQuestion(
        source_url="https://medofast.ir/qbank/topic-id-83?page=4",
        source_page=4,
        ordinal=4,
        stem_html=f"<p>{stem}</p>",
        stem_text=stem,
        options=options,
        correct_index=answer,
        answer_source=AnswerSource.MARKER if answer else AnswerSource.UNKNOWN,
        explanation_status=ExplanationStatus.UNKNOWN,
    )
    q.compute_content_hash()
    return q


def test_missing_questions_picks_only_ungradable_rows(tmp_path):
    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    answered = upsert_question(conn, _question("سوال با پاسخ", 2), topic_id=83)
    unanswered = upsert_question(conn, _question("سوال بدون پاسخ", None), topic_id=83)

    rows = missing_questions(conn)
    assert [row["id"] for row in rows] == [unanswered]
    assert answered not in [row["id"] for row in rows]
    assert rows[0]["signature"] == "گزینه1|گزینه2|گزینه3|گزینه4"
    conn.close()


def test_normalize_options_ignores_spacing_and_zwnj():
    spaced = normalize_options([{"text": "بیماری  های گلومرولی"},
                                {"text": "نارسایی\u200cکلیه"}])
    tight = normalize_options([{"text": "بیماری‌های گلومرولی"},
                               {"text": "نارسایی کلیه"}])
    assert spaced == tight
    assert spaced != normalize_options([{"text": "چیز دیگر"}])


def test_playwright_error_alias_comes_from_the_installed_version():
    """Regression: `PlaywrightError` is not a name in playwright.sync_api.

    Importing it (as this module once did, lazily) made every recovery run die
    with `cannot import name 'PlaywrightError'` before the first page load.
    """
    from playwright.sync_api import Error, sync_playwright

    from medo import answer_audit

    assert answer_audit.PlaywrightError is Error
    assert answer_audit.sync_playwright is sync_playwright


# ---------------------------------------------------------------------------
# The repair queue: what a signed-in pass can still improve
# ---------------------------------------------------------------------------

def test_repair_queue_covers_pages_read_as_a_guest(tmp_path):
    """A row with an answer is still queued when it was read as a guest."""
    from medo.answer_audit import repair_queue

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    upsert_question(conn, _question("سوال مهمان", 2), topic_id=83,
                    fetched_as="guest")
    pro = upsert_question(conn, _question("سوال پرو", 1), topic_id=83,
                          fetched_as="login")
    # A login page that already has its full lesson is complete.
    conn.execute("UPDATE questions SET explanation_text = 'درس', "
                 "explanation_status = 'full' WHERE id = ?", (pro,))
    conn.commit()
    queue = repair_queue(conn)
    assert [row["stem_text"] for row in queue] == ["سوال مهمان"]
    conn.close()


def test_repair_queue_keeps_rows_without_a_lesson(tmp_path):
    from medo.answer_audit import repair_queue

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    # Stored as a login page, but the lesson never arrived.
    upsert_question(conn, _question("بدون درس‌نامه", 2), topic_id=83,
                    fetched_as="login")
    assert len(repair_queue(conn)) == 1
    conn.close()


def test_repair_needs_a_login_and_says_so(tmp_path, monkeypatch):
    from medo import answer_audit

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    upsert_question(conn, _question("سوال مهمان", 2), topic_id=83,
                    fetched_as="guest")
    conn.close()
    monkeypatch.setattr(answer_audit, "has_storage_state", lambda *a, **k: False)

    messages = []
    result = answer_audit.audit_answers(tmp_path / "medo.db",
                                        on_log=messages.append)
    assert result["status"] == "auth_required"
    assert "وارد حساب" in messages[0]


def test_save_marks_the_row_as_login_and_keeps_the_votes(tmp_path):
    from medo import answer_audit
    from medo.db import get_question_by_id

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    qid = upsert_question(conn, _question("سوال مهمان", None), topic_id=83,
                          fetched_as="guest")
    row = dict(conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone())

    from medo.parser import parse_question_html
    page = (
        '<html><body><div id="qtxt"><p>سوال مهمان</p></div>'
        '<label id="label1"><span data-encoded-text="2KfZhtmF2YggMQ=="></span></label>'
        '<label id="label2"><span data-encoded-text="2KfZhtmF2YggMg=="></span></label>'
        '<div id="prograssBar1" data-encoded-percent="ODA="></div>'
        '<div id="block-b"><span>تایید گزینه ۲</span></div></body></html>')
    parsed = parse_question_html(page, submitted=True)
    answer_audit._save(conn, row, parsed)

    saved = get_question_by_id(conn, qid)
    assert saved["fetched_as"] == "login"
    assert saved["correct_index"] == 2
    assert saved["vote_percent"].get("1") == 80
    conn.close()


# ---------------------------------------------------------------------------
# Repair safety: never trade a stored lesson for a worse one, keep a backup
# ---------------------------------------------------------------------------

def _fake_page(text, status, correct=3, stem="سوال", options=None):
    """What a repair sees when a page comes back empty, locked or stubbed."""
    from medo.models import ParsedQuestion

    options = options or ("گزینه یک", "گزینه دو", "گزینه سه", "گزینه چهار")
    return ParsedQuestion(
        source_url="", source_page=4, ordinal=4,
        stem_html=f"<p>{stem}</p>", stem_text=stem,
        options=[ParsedOption(index=i + 1, text=t, raw_html="")
                 for i, t in enumerate(options)],
        correct_index=correct, site_question_id=1473,
        answer_source=AnswerSource.MARKER if correct else AnswerSource.UNKNOWN,
        explanation_html=f"<div>{text}</div>" if text else None,
        explanation_text=text,
        explanation_status=status,
    )


def _stored_full(conn, text="درس‌نامهٔ کامل و بلند " * 20, site_id=1473):
    """Store one question the way a good Pro pass leaves it."""
    q = _question("سوال با درس‌نامه", 2)
    q.site_question_id = site_id
    q.explanation_text = text
    q.explanation_html = f"<div>{text}</div>"
    q.explanation_status = ExplanationStatus.FULL
    qid = upsert_question(conn, q, topic_id=83, fetched_as="login")
    row = dict(conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone())
    return qid, row, text


def test_repair_keeps_a_full_lesson_when_the_page_returns_nothing(tmp_path):
    """An empty or locked page must not erase what the account already has."""
    from medo import answer_audit
    from medo.db import get_question_by_id

    for text, status in ((None, ExplanationStatus.UNKNOWN),
                         (None, ExplanationStatus.LOCKED),
                         ("کوتاه", ExplanationStatus.FULL)):
        conn = get_connection(tmp_path / f"medo-{status.value}-{bool(text)}.db")
        init_db(conn)
        qid, row, stored = _stored_full(conn)
        info = answer_audit._save(conn, row, _fake_page(text, status))
        saved = get_question_by_id(conn, qid)
        assert saved["explanation_text"] == stored
        assert saved["explanation_status"] == "full"
        assert info["explanation_kept"] is True
        conn.close()


def test_repair_accepts_a_full_lesson_that_is_at_least_as_long(tmp_path):
    from medo import answer_audit
    from medo.db import get_question_by_id

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    qid, row, stored = _stored_full(conn)
    longer = stored + " و این هم ادامهٔ درس است."
    info = answer_audit._save(conn, row, _fake_page(longer, ExplanationStatus.FULL))
    saved = get_question_by_id(conn, qid)
    assert saved["explanation_text"] == longer
    assert info["explanation_kept"] is False
    conn.close()


def test_refresh_text_rewrites_wording_but_not_identity(tmp_path):
    """A page matched by site id may refresh the text; the hash must not move."""
    from medo import answer_audit
    from medo.db import get_question_by_id

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    qid, row, stored = _stored_full(conn)
    old_hash = row["content_hash"]
    page = _fake_page(stored, ExplanationStatus.FULL,
                      stem="سوال با متن تازه", options=("الف", "ب", "ج", "د"))

    info = answer_audit._save(conn, row, page, refresh_text=True)
    saved = get_question_by_id(conn, qid)
    assert info["text_updated"] is True
    assert saved["stem_text"] == "سوال با متن تازه"
    assert "متن تازه" in saved["stem_html"]
    assert [o["text"] for o in saved["options"]] == ["الف", "ب", "ج", "د"]
    assert saved["content_hash"] == old_hash
    assert saved["site_question_id"] == 1473
    conn.close()


def test_signature_match_does_not_rewrite_the_text(tmp_path):
    """Without the site id the match is a guess; leave the wording alone."""
    from medo import answer_audit

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    qid, row, stored = _stored_full(conn)
    page = _fake_page(stored, ExplanationStatus.FULL, stem="متن دیگر")

    info = answer_audit._save(conn, row, page, refresh_text=False)
    saved = dict(conn.execute("SELECT * FROM questions WHERE id = ?", (qid,)).fetchone())
    assert info["text_updated"] is False
    assert saved["stem_text"] == "سوال با درس‌نامه"
    conn.close()


def test_backup_is_one_copy_per_process_and_prunes_the_oldest(tmp_path):
    from medo import backup

    backup.reset_backup_guard()
    db = tmp_path / "data" / "medo.db"
    db.parent.mkdir(parents=True)
    conn = get_connection(db)
    init_db(conn)
    upsert_question(conn, _question("سوال پشتیبان", 1), topic_id=83)
    conn.close()

    copy = backup.backup_database(db)
    assert copy is not None and copy.is_file()
    assert copy.parent.name == "backups"
    check = get_connection(copy)
    assert check.execute("SELECT count(*) FROM questions").fetchone()[0] == 1
    check.close()
    # The second call in the same process is a no-op.
    assert backup.backup_database(db) is None

    # Six older copies: the oldest is dropped, the newest five stay.
    for day in range(6):
        (copy.parent / f"medo-2026010{day + 1}-0100.db").write_bytes(b"old")
    backup._prune(copy.parent, "medo", 5)
    names = sorted(p.name for p in copy.parent.glob("medo-*.db"))
    assert "medo-20260101-0100.db" not in names
    assert len(names) == 5
    backup.reset_backup_guard()
