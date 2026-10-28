"""tests/test_live_report.py — the pasteable summary must stay paid-text free."""
from medo.db import (get_connection, init_db, upsert_category,
                     upsert_question)
from medo.live_report import build_report, db_facts, log_facts
from medo.models import (AnswerSource, ExplanationStatus, ParsedOption,
                         ParsedQuestion)

SENTINEL = "متن-درس-خصوصی-که-نباید-در-گزارش-بیاید"


def _question(stem: str, answer: int | None, status=ExplanationStatus.UNKNOWN,
              text: str | None = None) -> ParsedQuestion:
    q = ParsedQuestion(
        source_url="https://medofast.ir/qbank/topic-id-78?page=2",
        source_page=2, ordinal=2,
        stem_html=f"<p>{stem}</p>", stem_text=stem,
        options=[ParsedOption(index=i, text=f"گزینه {i}", raw_html="")
                 for i in range(1, 5)],
        correct_index=answer,
        answer_source=AnswerSource.MARKER if answer else AnswerSource.UNKNOWN,
        explanation_html=f"<div>{text}</div>" if text else None,
        explanation_text=text,
        explanation_status=status,
    )
    q.compute_content_hash()
    return q


def _db(tmp_path):
    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    # Needs repair: read as a guest, no answer.
    upsert_question(conn, _question(SENTINEL, None), topic_id=78,
                    tag_id=1470, fetched_as="guest")
    # Complete: answer + full lesson from the signed-in session.
    complete = _question("سوال کامل", 2, ExplanationStatus.FULL, "درس " * 20)
    complete.site_question_id = 1473
    upsert_question(conn, complete, topic_id=78, tag_id=1470,
                    fetched_as="login")
    # Needs repair: has a key, but the lesson never arrived.  No node either.
    upsert_question(conn, _question("سوال بی‌گره", 1), fetched_as="login")

    upsert_category(conn, kind="subtopic", title="نفرولوژی",
                    source_url="/qbank/topic-id-78?tag=1470", position=1)
    conn.execute("UPDATE categories SET question_count = 5 "
                 "WHERE source_url = '/qbank/topic-id-78?tag=1470'")
    conn.commit()
    return conn


def _log(tmp_path) -> str:
    path = tmp_path / "medo.log"
    path.write_text(
        "2026-10-27 16:58:27 [ui] tag   930: stopped at page 1/? - page 1: HTTP 429\n"
        "2026-10-28 18:08:25 [pace] 50 clean pages — 1.4-2.7s per page\n"
        "2026-10-28 17:56:31 [position] requested page 30, site shows 1\n"
        "2026-10-28 19:24:57 [i] Topic 78 tag 1465: requested page 1, site is at 7\n"
        f"2026-10-28 19:25:00 [scrape] leak: {SENTINEL}\n"
        '  File "E:\\x\\_page.py", line 429, in wait_for_selector\n',
        encoding="utf-8")
    return str(path)


def test_log_facts_ignore_tracebacks_and_keep_only_numbers(tmp_path):
    facts = log_facts(_log(tmp_path))
    assert facts["drift"] == 2
    assert facts["drift_position"] == 1 and facts["drift_harvest"] == 1
    assert (facts["last_requested"], facts["last_shown"]) == (1, 7)
    assert facts["throttles"] == 1          # the traceback's "line 429" is not one
    assert facts["last_429"] == "2026-10-27 16:58:27"
    assert facts["pace"] == (1.4, 2.7)


def test_report_counts_the_archive_and_never_prints_lesson_text(tmp_path):
    conn = _db(tmp_path)
    facts = db_facts(conn)
    conn.close()

    assert facts["total"] == 3
    assert facts["with_nodes"] == 2 and facts["without_nodes"] == 1
    assert facts["with_site_id"] == 1 and facts["without_site_id"] == 2
    assert facts["repair_queue"] == 2
    assert (facts["subtopics"], facts["subtopics_started"],
            facts["subtopics_done"], facts["subtopics_incomplete"]) == (1, 1, 0, 1)
    assert [(s["tag"], s["stored"], s["declared"]) for s in facts["worst"]] == [
        (1470, 2, 5)]

    text = build_report(
        tmp_path / "medo.db", _log(tmp_path),
        doctor={"verdict": "ready", "message": "آماده استخراج.",
                "checks": [{"name": "tls", "ok": True}]})
    assert "doctor: ready (tls=ok)" in text
    assert "pages: 2 drift event(s)" in text
    assert "topic 78 tag 1470: 2/5" in text
    assert "questions: 3 total; nodes 2 have / 1 missing" in text
    assert "site id 1 have / 2 missing" in text
    assert "repair queue: 2" in text
    assert "429s: 1 event(s), last 2026-10-27 16:58:27" in text
    assert "pace: 1.4-2.7 s per page" in text
    assert SENTINEL not in text


def test_report_survives_a_missing_database_and_log(tmp_path):
    text = build_report(tmp_path / "missing.db", tmp_path / "missing.log")
    assert "database not found" in text
    assert "doctor: not run" in text           # no doctor dict was passed
    assert "pace: 1.5-3.0 s per page" in text
