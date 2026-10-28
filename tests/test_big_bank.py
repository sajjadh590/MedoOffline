"""tests/test_big_bank.py — the stress generator must never touch real data."""
import importlib.util
from pathlib import Path

import pytest

from medo.db import DEFAULT_DB_PATH, get_connection


def _generator():
    path = Path(__file__).resolve().parents[1] / "scripts" / "dev" / "make_big_bank.py"
    spec = importlib.util.spec_from_file_location("make_big_bank", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    return _generator()


def test_the_real_bank_is_refused(generator):
    with pytest.raises(SystemExit):
        generator.build(DEFAULT_DB_PATH, count=1)
    assert DEFAULT_DB_PATH.name == "medo.db"


def test_a_small_generated_bank_is_internally_consistent(generator, tmp_path):
    db = tmp_path / "stress.db"
    assert generator.build(db, count=40, topics=4) == 40

    conn = get_connection(db)
    assert conn.execute("SELECT count(*) FROM categories WHERE kind='topic'"
                        ).fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM categories WHERE kind='subtopic'"
                        ).fetchone()[0] == 4
    assert conn.execute("SELECT count(*) FROM question_nodes").fetchone()[0] == 80
    assert conn.execute(
        "SELECT count(*) FROM questions WHERE explanation_status='full' "
        "AND explanation_text IS NOT NULL AND site_question_id IS NOT NULL"
    ).fetchone()[0] == 40
    # Re-running without --reset must not silently double the archive.
    conn.close()
    with pytest.raises(SystemExit):
        generator.build(db, count=40, topics=4)
    assert generator.build(db, count=40, topics=4, reset=True) == 40
