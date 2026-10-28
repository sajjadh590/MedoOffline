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
