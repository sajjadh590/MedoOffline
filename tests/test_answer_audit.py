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
