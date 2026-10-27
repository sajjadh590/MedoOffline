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
