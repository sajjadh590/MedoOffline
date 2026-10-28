"""Map downloaded questions to their subtopics without re-downloading them.

Why this exists
---------------
A topic page (``/qbank/topic-id-N``) returns every question of the topic, and
its subtopic pages (``?tag=T``) return a filtered slice.  The site only reveals
which slice a question belongs to on the *filtered* page, so a bank built from
the unfiltered topic has ``tag_id = NULL`` everywhere and every subtopic row
looks empty.

This script walks the filtered pages of one topic, reads each question's
options (the stem is encrypted until the page's JS runs, but options are
plain), and matches it to the stored question by an option signature.  It only
adds a ``question_nodes`` row (plus the legacy ``tag_id``/``subtopic_title``
when they are still empty); stems, answers and progress stay untouched, and
nothing is downloaded twice.

Usage
-----
    python scripts/backfill_tags.py --topic 1
    python scripts/backfill_tags.py --topic 1 --dry-run
    python scripts/backfill_tags.py --all --delay 0.15

It is safe to re-run: already-mapped questions are kept, and a signature that
matches two different questions is skipped instead of guessed.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medo.auth import DEFAULT_STORAGE_STATE, has_storage_state  # noqa: E402
from medo.browser import new_context  # noqa: E402
from medo.db import count_node_questions, get_connection  # noqa: E402
from medo.netfix import resolve_public_ip  # noqa: E402
from medo.parser import parse_question_html, _clean_text  # noqa: E402

BASE = "https://medofast.ir"


def signature(options) -> str:
    """Stable fingerprint of the option texts (spacing/ZWNJ insensitive)."""
    from medo.text_utils import strip_invisible

    parts = []
    for option in options:
        text = strip_invisible(_clean_text(option.text or ""))
        parts.append(re.sub(r"\s+", "", text))
    return "|".join(parts)


def load_bank(conn, topic_id: int) -> dict[str, list[int]]:
    bank: dict[str, list[int]] = defaultdict(list)
    for row in conn.execute(
        "SELECT id, options_json FROM questions WHERE topic_id = ?", (topic_id,)
    ):
        try:
            options = json.loads(row["options_json"] or "[]")
        except ValueError:
            continue
        parts = []
        for option in options:
            text = _clean_text(str(option.get("text") or ""))
            parts.append(re.sub(r"\s+", "", text))
        if parts:
            bank["|".join(parts)].append(int(row["id"]))
    return bank


def subtopics(conn, topic_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT title, source_url, question_count FROM categories "
        "WHERE source_url LIKE ? ORDER BY position, id",
        (f"%topic-id-{topic_id}?tag=%",),
    ).fetchall()
    out = []
    for row in rows:
        match = re.search(r"[?&]tag=(\d+)", row["source_url"] or "")
        if not match:
            continue
        out.append({
            "tag_id": int(match.group(1)),
            "title": row["title"],
            "declared": int(row["question_count"] or 0),
        })
    return out


class Throttled(RuntimeError):
    """The site answered 429 (or a soft-blocked HTML page) and kept doing it."""


def fetch_json(page, topic_id: int, tag_id: int, page_no: int, timeout_ms: int = 25000,
               pause: float = 30.0, tries: int = 2):
    script = """async (args) => {
        const url = `/qbank/topic-id-${args.topic}?tag=${args.tag}&page=${args.page}`;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), args.timeout);
        try {
            const r = await fetch(url, {
                headers: {'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json'},
                credentials: 'same-origin',
                signal: controller.signal,
            });
            const text = await r.text();
            return {status: r.status, retryAfter: r.headers.get('retry-after') || '',
                    ctype: r.headers.get('content-type') || '', text};
        } finally {
            clearTimeout(timer);
        }
    }"""
    args = {
        "topic": int(topic_id), "tag": int(tag_id),
        "page": int(page_no), "timeout": int(timeout_ms),
    }
    for attempt in range(1, tries + 1):
        result = page.evaluate(script, args)
        status = result.get("status")
        if status == 200:
            try:
                return json.loads(result["text"])
            except ValueError:
                pass       # a soft block: the normal HTML page instead of JSON
        elif status != 429:
            # A missing/renamed tag is a per-tag problem; throttling is not.
            raise RuntimeError(f"page {page_no}: HTTP {status}")
        if attempt == tries:
            raise Throttled(f"page {page_no} after {tries} tries")
        wait = _wait_after_throttle(result.get("retryAfter"), pause, attempt)
        print(f"    … throttled at page {page_no}; waiting {wait:.0f}s "
              f"(try {attempt + 1}/{tries})", flush=True)
        time.sleep(wait)
    raise Throttled(f"page {page_no}")


def _wait_after_throttle(retry_after, pause: float, attempt: int) -> float:
    """Seconds to wait after a 429 / soft block.

    ``Retry-After`` wins when the site sends it; otherwise back off
    exponentially (pause, 2×, 4×…) so a rate-limited run slows down instead of
    burning the remaining allowance.
    """
    wait = pause
    try:
        header = float(str(retry_after).strip())
        if header > 0:
            wait = header
    except (TypeError, ValueError):
        wait = pause * (2 ** (attempt - 1))
    return min(max(wait, 5.0), 300.0)


def tagged_count(conn, topic_id: int, tag_id: int) -> int:
    """Questions already mapped to this subtopic."""
    return count_node_questions(conn, int(topic_id), int(tag_id))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", type=int, action="append", default=None,
                        help="topic id (repeatable)")
    parser.add_argument("--all", action="store_true",
                        help="every topic that has subtopics in the database")
    parser.add_argument("--delay", type=float, default=1.2,
                        help="seconds between page fetches (default 1.2; the site "
                             "throttles bursts of the filtered endpoint)")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="stop after N filtered pages per subtopic (0 = all)")
    parser.add_argument("--pause", type=float, default=45.0,
                        help="seconds to wait after the site starts throttling "
                             "(429 / HTML instead of JSON); default 45")
    parser.add_argument("--refetch", action="store_true",
                        help="walk subtopics that are already mapped again "
                             "(default: they are skipped, so a re-run continues "
                             "where the last one stopped)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the mapping without writing it")
    parser.add_argument("--db", default=None, help="database path")
    args = parser.parse_args()

    conn = get_connection(args.db) if args.db else get_connection()
    topics = args.topic or []
    if args.all or not topics:
        topics = [int(r[0]) for r in conn.execute(
            "SELECT DISTINCT q.topic_id FROM questions q "
            "WHERE q.topic_id IS NOT NULL AND q.topic_id > 0 "
            "ORDER BY q.topic_id"
        ) if subtopics(conn, int(r[0]))]
    if not topics:
        print("[!] no topic with subtopics found")
        return 1

    from playwright.sync_api import sync_playwright

    ip = resolve_public_ip("medofast.ir")
    chrome_args = [
        "--no-proxy-server",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run", "--no-default-browser-check",
    ]
    if ip:
        chrome_args.append(f"--host-resolver-rules=MAP medofast.ir {ip}")
        print(f"[i] system DNS is fake; pinned medofast.ir -> {ip}")

    mapped_total = 0
    skipped_done = 0
    stopped_early = None
    leftovers: list[dict] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True, args=chrome_args)
            # The filtered JSON endpoint answers a guest only within the daily
            # allowance; reuse the saved session when there is one.
            context = new_context(
                browser,
                storage_state=(str(DEFAULT_STORAGE_STATE)
                               if has_storage_state() else None),
            )
            page = context.new_page()

            # Warm the session on a real page: the JSON endpoints reuse its
            # cookies/tokens. Any topic page will do.
            first = subtopics(conn, topics[0])
            if not first:
                print(f"[!] topic {topics[0]} has no subtopics")
                return 1
            try:
                page.goto(
                    f"{BASE}/qbank/topic-id-{topics[0]}?tag={first[0]['tag_id']}",
                    wait_until="networkidle", timeout=60000)
            except Exception as exc:
                # No point walking tag by tag when the site does not answer at
                # all: say it once instead of 39 identical failures.
                print("[!] سایت باز نشد "
                      f"({str(exc).splitlines()[0][:120]}) — اتصال/VPN را بررسی "
                      "کن و بعد دوباره همین دکمه را بزن. چیزی از دست نرفت.")
                return 1

            for topic_id in topics:
                bank = load_bank(conn, topic_id)

                subs = subtopics(conn, topic_id)
                print(f"\n[topic {topic_id}] {len(bank)} question signature(s), "
                      f"{len(subs)} subtopic(s)")
                progress = int(conn.execute(
                    "SELECT count(DISTINCT n.question_id) FROM question_nodes n "
                    "WHERE n.topic_id = ? AND n.tag_id > 0", (topic_id,)
                ).fetchone()[0] or 0)
                print(f"  already tagged: {progress}")

                assignments: dict[int, dict] = {}

                def flush(ids: list[int]) -> None:
                    """Write one subtopic's matches and commit right away.

                    The whole-topic commit meant a 429 on subtopic 4 discarded
                    the mapping of subtopics 1-3, and the next run paid for
                    them again.

                    Membership goes into question_nodes: a question that sits
                    in two subtopics keeps both (questions.tag_id could hold
                    only one, so the second used to be dropped as a
                    «conflict»).
                    """
                    nonlocal mapped_total
                    updates = 0
                    for question_id in ids:
                        payload = assignments[question_id]
                        updates += 1
                        if args.dry_run:
                            continue
                        conn.execute(
                            "INSERT OR IGNORE INTO question_nodes "
                            "(question_id, topic_id, tag_id, subtopic_title) "
                            "VALUES (?, ?, ?, ?)",
                            (question_id, int(topic_id), payload["tag_id"],
                             payload["title"]),
                        )
                        # Old single-parent column: the first tag wins, so a
                        # reader that has not moved to nodes stays stable.
                        conn.execute(
                            "UPDATE questions SET "
                            "  tag_id = CASE WHEN tag_id IS NULL THEN ? ELSE tag_id END, "
                            "  subtopic_title = coalesce(subtopic_title, ?), "
                            "  updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                            (payload["tag_id"], payload["title"], question_id))
                    if not args.dry_run:
                        conn.commit()
                    mapped_total += updates

                for sub in subs:
                    total_pages = 0
                    fetched = 0
                    matched = 0
                    matched_ids: list[int] = []
                    done = tagged_count(conn, topic_id, sub["tag_id"])
                    if sub["declared"] and done >= sub["declared"] and not args.refetch:
                        skipped_done += 1
                        continue
                    try:
                        first_page = fetch_json(page, topic_id, sub["tag_id"], 1,
                                               pause=args.pause)
                        total_pages = int((first_page.get("pagination") or {})
                                          .get("total_pages") or 0)
                        html_items = [(1, first_page.get("html") or "")]
                        if args.max_pages:
                            total_pages = min(total_pages, args.max_pages)
                        for page_no in range(2, total_pages + 1):
                            time.sleep(max(0.0, args.delay))
                            payload = fetch_json(page, topic_id, sub["tag_id"], page_no,
                                                 pause=args.pause)
                            html_items.append((page_no, payload.get("html") or ""))
                        for page_no, html in html_items:
                            fetched += 1
                            parsed = parse_question_html(
                                html, submitted=False, source_url="",
                                source_page=page_no, ordinal=page_no)
                            if not parsed.options:
                                continue
                            ids = bank.get(signature(parsed.options))
                            if not ids:
                                continue
                            for question_id in ids:
                                if question_id in assignments:
                                    continue
                                assignments[question_id] = {
                                    "tag_id": sub["tag_id"], "title": sub["title"],
                                }
                                matched_ids.append(question_id)
                                matched += 1
                        print(f"  tag {sub['tag_id']:>5} «{sub['title'][:38]}»: "
                              f"{fetched} page(s), {matched} match(es)")
                        flush(matched_ids)
                    except Throttled:
                        raise          # the whole run has to stop, not just this tag
                    except Exception as exc:
                        print(f"  tag {sub['tag_id']:>5}: stopped at page "
                              f"{fetched + 1}/{total_pages or '?'} — {exc}")

                print(f"  -> {len(assignments)} question(s) mapped for this topic")
            browser.close()
    except Throttled as exc:
        stopped_early = exc
    finally:
        try:
            # «Without a subtopic» = no subtopic node in its own topic, which
            # is exact even when the same question is tagged elsewhere.
            leftovers = [dict(r) for r in conn.execute(
                "SELECT q.topic_id, count(*) AS n FROM questions q "
                "WHERE q.topic_id IS NOT NULL "
                "  AND NOT EXISTS (SELECT 1 FROM question_nodes x "
                "                  WHERE x.question_id = q.id "
                "                    AND x.topic_id = q.topic_id AND x.tag_id > 0) "
                "GROUP BY q.topic_id ORDER BY n DESC")]
        except Exception:
            leftovers = []
        conn.close()

    if stopped_early:
        print(f"\n[!] the site started throttling this connection ({stopped_early}).")
        print("    Whatever was mapped before that is saved; run again later —")
        print("    subtopics that already finished are skipped, so it continues.")
    print(f"\n[done] mapped {mapped_total} question(s)"
          + (f", {skipped_done} subtopic(s) already complete" if skipped_done else ""))
    if leftovers and not args.dry_run:
        print("[i] questions still without a subtopic:")
        for row in leftovers:
            print(f"    topic {row['topic_id']}: {row['n']}")
    if args.dry_run:
        print("      (dry run — nothing was written)")
    return 1 if stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
