"""tests/test_backfill_tags.py — the subtopic mapper's pacing rules.

The script itself needs the live site, so only the pure parts run here: how a
throttled request waits, and how a re-run recognises subtopics that are already
mapped (the reasons the first attempts mapped nothing but 429s).
"""
import importlib.util
from pathlib import Path

import pytest

from medo.db import get_connection, init_db, upsert_question
from medo.models import AnswerSource, ExplanationStatus, ParsedOption, ParsedQuestion


def _backfill():
    path = Path(__file__).resolve().parents[1] / "scripts" / "backfill_tags.py"
    spec = importlib.util.spec_from_file_location("backfill_tags", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def backfill():
    return _backfill()


def test_wait_after_throttle_honours_retry_after(backfill):
    assert backfill._wait_after_throttle("12", 45.0, 1) == 12.0
    # A hostile header must not stall the run for hours.
    assert backfill._wait_after_throttle("9999", 45.0, 2) == 300.0


def test_wait_after_throttle_backs_off_exponentially(backfill):
    assert backfill._wait_after_throttle("", 20.0, 1) == 20.0
    assert backfill._wait_after_throttle(None, 20.0, 2) == 40.0
    assert backfill._wait_after_throttle("junk", 20.0, 3) == 80.0
    # ...but never below the floor, so a retry is not instant.
    assert backfill._wait_after_throttle("1", 45.0, 1) == 5.0


def _question(stem: str) -> ParsedQuestion:
    options = [ParsedOption(index=i, text=f"گزینه {i}", raw_html="", vote_percent=None)
               for i in range(1, 5)]
    q = ParsedQuestion(
        source_url="https://medofast.ir/qbank/topic-id-78?page=1",
        source_page=1,
        ordinal=1,
        stem_html=f"<p>{stem}</p>",
        stem_text=stem,
        options=options,
        correct_index=1,
        answer_source=AnswerSource.MARKER,
        explanation_status=ExplanationStatus.UNKNOWN,
    )
    q.compute_content_hash()
    return q


def test_tagged_count_measures_progress_per_subtopic(backfill, tmp_path):
    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    upsert_question(conn, _question("سوال الف"), topic_id=78, tag_id=1470,
                    subtopic_title="نفرولوژی کودکان")
    upsert_question(conn, _question("سوال ب"), topic_id=78, tag_id=1470,
                    subtopic_title="نفرولوژی کودکان")
    upsert_question(conn, _question("سوال ج"), topic_id=78)

    assert backfill.tagged_count(conn, 78, 1470) == 2
    assert backfill.tagged_count(conn, 78, 1463) == 0
    conn.close()


# ---------------------------------------------------------------------------
# Identity: site id first, signature only when it is unambiguous
# ---------------------------------------------------------------------------

def test_match_uses_the_site_id_even_when_the_options_changed(backfill, tmp_path):
    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    q = _question("سوال الف")
    q.site_question_id = 1473
    qid = upsert_question(conn, q, topic_id=78)

    parsed = _question("سوال الف")            # same options, but a new id row
    parsed.site_question_id = 1473
    parsed.options = parsed.options[:3]        # options edited upstream
    assert backfill.match_question(conn, parsed, {}) == (qid, "site")
    conn.close()


def test_ambiguous_signature_is_skipped_not_assigned(backfill, tmp_path):
    """Two stored questions with identical options must never both get a tag."""
    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    first = _question("سوال الف")
    second = _question("سوال ب")               # same 4 option texts
    upsert_question(conn, first, topic_id=78)
    upsert_question(conn, second, topic_id=78)

    parsed = _question("سوال الف")             # no site id on the fragment
    question_id, why = backfill.match_question(conn, parsed, backfill.load_bank(conn, 78))
    assert question_id is None
    assert why.startswith("ambiguous")
    conn.close()


def test_save_membership_adds_a_node_and_fills_the_site_id(backfill, tmp_path):
    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    q = _question("سوال الف")
    qid = upsert_question(conn, q, topic_id=78)

    backfill.save_membership(conn, qid, 78, 1470, "نفرولوژی کودکان", 1473, False)
    conn.commit()
    row = conn.execute(
        "SELECT tag_id, subtopic_title, site_question_id FROM questions WHERE id = ?",
        (qid,)).fetchone()
    assert row["tag_id"] == 1470
    assert row["subtopic_title"] == "نفرولوژی کودکان"
    assert row["site_question_id"] == 1473

    # A second subtopic is added without overwriting the legacy columns.
    backfill.save_membership(conn, qid, 78, 1500, "دیالیز", None, False)
    conn.commit()
    row = conn.execute(
        "SELECT tag_id, site_question_id FROM questions WHERE id = ?", (qid,)).fetchone()
    assert row["tag_id"] == 1470
    assert row["site_question_id"] == 1473
    tags = {r[0] for r in conn.execute(
        "SELECT tag_id FROM question_nodes WHERE question_id = ?", (qid,))}
    # 0 is the whole-topic node the first download stored.
    assert tags == {0, 1470, 1500}
    conn.close()


def test_tag_sync_cursor_is_per_subtopic_and_resumable(tmp_path):
    from medo.db import get_tag_sync_state, update_tag_sync_state

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    update_tag_sync_state(conn, 78, 1470, last_page=7, status="running")
    update_tag_sync_state(conn, 78, 1500, last_page=2, status="throttled")

    assert get_tag_sync_state(conn, 78, 1470)["last_page"] == 7
    assert get_tag_sync_state(conn, 78, 1500)["status"] == "throttled"
    # A whole-topic pass (tag 0) has its own row, never the subtopic's.
    assert get_tag_sync_state(conn, 78, 0) is None
    update_tag_sync_state(conn, 78, 0, last_page=3, status="paused")
    assert get_tag_sync_state(conn, 78, 1470)["last_page"] == 7
    conn.close()


def test_subtopic_targets_follow_the_tree_order(tmp_path):
    from medo.db import subtopic_targets, upsert_category

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)
    upsert_category(conn, kind="topic", title="کلیه",
                    source_url="/qbank/topic-id-78", position=0)
    upsert_category(conn, kind="subtopic", title="دوم",
                    source_url="/qbank/topic-id-78?tag=1500", position=2)
    upsert_category(conn, kind="subtopic", title="اول",
                    source_url="/qbank/topic-id-78?tag=1470", position=1)

    assert [(t["tag_id"], t["subtopic_title"]) for t in subtopic_targets(conn, 78)] == [
        (1470, "اول"), (1500, "دوم")]
    assert subtopic_targets(conn, 79) == []
    conn.close()
