#!/usr/bin/env python
"""Build a synthetic archive for load/heap stress tests.

Real content never leaves ``data/`` and the owner's bank is far from 20,000
questions, so the only honest way to know how the UI, the phone bundle and the
Android app behave at that size is to generate text that is obviously fake:

    python scripts/dev/make_big_bank.py --db build/stress/medo.db --reset

Every question is «متن ساختگی …» filler with four fake options and a ~700-char
lesson.  The generator refuses to touch ``data/medo.db``; pass a path under
``build/`` (git-ignored) and re-run with ``--reset`` to replace it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from medo.db import DEFAULT_DB_PATH, get_connection, init_db  # noqa: E402

FAKE_START = "این متن ساختگی است و فقط برای سنجش بار برنامه نوشته شده است. "
# A wide fake vocabulary (instead of one repeated paragraph) so the bundle
# compresses like real prose and every lesson is a distinct string in memory.
VOCAB = [f"واژه{i}" for i in range(600)]
OPTIONS = ("گزینهٔ ساختگی الف", "گزینهٔ ساختگی ب",
           "گزینهٔ ساختگی ج", "گزینهٔ ساختگی د")


def _sentence(rng: random.Random, chars: int = 90) -> str:
    parts = [FAKE_START]
    length = len(FAKE_START)
    while length < chars:
        word = rng.choice(VOCAB)
        parts.append(word)
        length += len(word) + 1
    return " ".join(parts)


def build(db_path: Path | str, count: int = 20_000, topics: int = 40,
          reset: bool = False) -> int:
    """(Re)create `db_path` with `count` fake questions; returns the count."""
    path = Path(db_path)
    if path.resolve() == Path(DEFAULT_DB_PATH).resolve():
        raise SystemExit(" refusing to overwrite the real bank; use --db build/...")
    if count <= 0 or topics <= 0:
        raise SystemExit("--count and --topics must be positive")

    conn = get_connection(path)
    init_db(conn)
    existing = conn.execute("SELECT count(*) FROM questions").fetchone()[0]
    if existing and not reset:
        conn.close()
        raise SystemExit(f"{path} already holds {existing} question(s); "
                         "pass --reset or choose another path")
    if reset:
        for table in ("question_assets", "question_nodes", "questions",
                      "categories", "tag_sync_state", "micro_state"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()

    rng = random.Random(20261028)
    per_topic = max(1, count // topics)
    per_subtopic = max(1, per_topic // 2)
    rows = conn.execute("SELECT count(*) FROM questions").fetchone()[0]
    qid = int(rows or 0)

    started = time.perf_counter()
    for t in range(topics):
        tid = t + 1
        topic_url = f"/qbank/topic-id-{tid}"
        cur = conn.execute(
            "INSERT INTO categories (kind, title, source_url, position, question_count) "
            "VALUES ('topic', ?, ?, ?, ?)",
            (f"مبحث ساختگی {tid}", topic_url, t, per_topic))
        parent = cur.lastrowid
        tag = 1000 + tid * 2
        conn.execute(
            "INSERT INTO categories (parent_id, kind, title, source_url, position, "
            "question_count) VALUES (?, 'subtopic', ?, ?, 1, ?)",
            (parent, f"زیرمبحث ساختگی {tid}", f"{topic_url}?tag={tag}",
             per_subtopic))

        batch = []
        nodes = []
        for n in range(per_topic):
            qid += 1
            page = n // 5 + 1
            stem = f"سؤال ساختگی {qid} — {_sentence(rng, 60)}"
            lesson = _sentence(rng, 700)
            options = [{"index": i + 1, "text": text}
                       for i, text in enumerate(OPTIONS)]
            site_id = 200_000 + qid
            batch.append((
                qid, tid, tag,
                f"https://medofast.ir{topic_url}?tag={tag}", page, n % 5 + 1,
                hashlib.sha256(f"synthetic-{qid}".encode()).hexdigest(),
                f"<p>{stem}</p>", stem,
                json.dumps(options, ensure_ascii=False),
                n % 4 + 1, "marker", "{}",
                f"<div>{lesson}</div>", lesson, "full", 0,
                site_id, "login",
            ))
            nodes.append((qid, tid, 0, None))
            nodes.append((qid, tid, tag, f"زیرمبحث ساختگی {tid}"))
        conn.executemany(
            "INSERT INTO questions (id, topic_id, tag_id, source_url, source_page, "
            "ordinal, content_hash, stem_html, stem_text, options_json, "
            "correct_index, answer_source, vote_percent_json, explanation_html, "
            "explanation_text, explanation_status, has_images, site_question_id, "
            "fetched_as) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            batch)
        conn.executemany(
            "INSERT INTO question_nodes (question_id, topic_id, tag_id, "
            "subtopic_title) VALUES (?, ?, ?, ?)", nodes)
        conn.commit()

    total = conn.execute("SELECT count(*) FROM questions").fetchone()[0]
    conn.close()
    size = path.stat().st_size / 1048576
    print(f"[big-bank] {total} question(s), {topics} topic(s), "
          f"{size:.1f} MB, {time.perf_counter() - started:.1f}s -> {path}")
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "build/stress/medo.db")
    parser.add_argument("--count", type=int, default=20_000)
    parser.add_argument("--topics", type=int, default=40)
    parser.add_argument("--reset", action="store_true",
                        help="delete the existing synthetic rows first")
    args = parser.parse_args()
    try:
        build(args.db, args.count, args.topics, args.reset)
    except SystemExit as exc:
        print(f"[!]{exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
