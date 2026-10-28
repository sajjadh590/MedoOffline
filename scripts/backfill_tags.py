"""Map downloaded questions to their subtopics without re-downloading them.

Why this exists
---------------
A topic page (``/qbank/topic-id-N``) returns every question of the topic, and
its subtopic pages (``?tag=T``) return a filtered slice.  The site only reveals
which slice a question belongs to on the *filtered* page, so a bank built from
the unfiltered topic has no subtopic membership and every subtopic row looks
empty.

This script walks the filtered pages of one topic and matches each question to
its stored row:

1. the site's own question id (``#question-meta``) when the page carries one,
2. the option signature ONLY when it belongs to exactly one stored question.
   An ambiguous signature is skipped and counted — never assigned to several
   questions just because their options look alike.

Every match is written (and committed) as a ``question_nodes`` row as soon as
its page is read, so an HTTP 429 costs at most the page in flight; the cursor
in ``tag_sync_state`` makes a re-run resume at that exact page.

Usage
-----
    python scripts/backfill_tags.py --topic 1
    python scripts/backfill_tags.py --topic 1 --dry-run
    python scripts/backfill_tags.py --all
    python scripts/backfill_tags.py --site-ids --all

``--site-ids`` fills the missing site question id of the rows stored before
this identity existed: it re-reads only the whole-topic pages those rows came
from and matches them by their options.

It is safe to re-run: finished subtopics are skipped and a page that was
already mapped writes the same rows again (INSERT OR IGNORE).
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
from medo.browser import launch_browser, new_context  # noqa: E402
from medo.db import (  # noqa: E402
    DEFAULT_DB_PATH,
    count_node_questions,
    get_connection,
    get_tag_sync_state,
    init_db,
    update_tag_sync_state,
)
from medo.parser import parse_question_html, _clean_text  # noqa: E402

BASE = "https://medofast.ir"
MIN_DELAY = 1.5          # the site throttles faster walks than this


def signature(options) -> str:
    """Stable fingerprint of the option texts (spacing/ZWNJ insensitive)."""
    from medo.text_utils import strip_invisible

    parts = []
    for option in options:
        text = strip_invisible(_clean_text(option.text or ""))
        parts.append(re.sub(r"\s+", "", text))
    return "|".join(parts)


def load_bank(conn, topic_id: int) -> dict[str, list[int]]:
    """Option signature -> stored question ids of this topic."""
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


def fetch_json(page, topic_id: int, tag_id: int | None, page_no: int,
               timeout_ms: int = 25000, pause: float = 30.0, tries: int = 2):
    """One filtered (or whole-topic) JSON page, honouring Retry-After."""
    script = """async (args) => {
        const query = args.tag > 0
            ? `?tag=${args.tag}&page=${args.page}`
            : `?page=${args.page}`;
        const url = `/qbank/topic-id-${args.topic}${query}`;
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
        "topic": int(topic_id), "tag": int(tag_id or 0),
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


def match_question(conn, parsed, by_signature: dict[str, list[int]]):
    """(question id, how) for one parsed page, or (None, why not)."""
    site_id = getattr(parsed, "site_question_id", None)
    if site_id:
        row = conn.execute(
            "SELECT id FROM questions WHERE site_question_id = ?", (int(site_id),)
        ).fetchone()
        if row:
            return int(row["id"]), "site"
    if not parsed.options:
        return None, "no options"
    ids = by_signature.get(signature(parsed.options)) or []
    if len(ids) == 1:
        return ids[0], "signature"
    if len(ids) > 1:
        return None, f"ambiguous ({len(ids)} stored questions share these options)"
    return None, "not stored"


def save_membership(conn, question_id: int, topic_id: int, tag_id: int,
                    subtopic_title: str | None,
                    site_id: int | None, dry_run: bool) -> None:
    """Add one node (and fill the legacy columns / the site id) for a match."""
    if dry_run:
        return
    conn.execute(
        "INSERT OR IGNORE INTO question_nodes "
        "(question_id, topic_id, tag_id, subtopic_title) VALUES (?, ?, ?, ?)",
        (question_id, int(topic_id), int(tag_id), subtopic_title),
    )
    conn.execute(
        "UPDATE questions SET "
        "  tag_id = CASE WHEN tag_id IS NULL THEN ? ELSE tag_id END, "
        "  subtopic_title = coalesce(subtopic_title, ?), "
        "  site_question_id = coalesce(site_question_id, ?), "
        "  updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (int(tag_id) if tag_id else None, subtopic_title, site_id, question_id),
    )


def map_subtopics(conn, page, topic_id: int, args, delay: float, stats: dict) -> bool:
    """Walk every subtopic of one topic. False when the run must stop."""
    by_signature = load_bank(conn, topic_id)
    subs = subtopics(conn, topic_id)
    print(f"\n[topic {topic_id}] {len(by_signature)} question signature(s), "
          f"{len(subs)} subtopic(s)")
    if not subs:
        print(f"  no subtopic rows stored for topic {topic_id}")
        return True

    for sub in subs:
        tag = int(sub["tag_id"])
        state = get_tag_sync_state(conn, topic_id, tag) or {}
        done = tagged_count(conn, topic_id, tag)
        if state.get("status") == "completed" and not args.refetch:
            stats["skipped_done"] += 1
            continue
        if not state and sub["declared"] and done >= sub["declared"] and not args.refetch:
            stats["skipped_done"] += 1
            continue

        start = 1
        if state.get("status") in {"running", "paused", "throttled"} and not args.refetch:
            start = max(1, int(state.get("last_page") or 0) + 1)

        try:
            first = fetch_json(page, topic_id, tag, 1, pause=args.pause)
            total_pages = int((first.get("pagination") or {}).get("total_pages") or 0)
            if args.max_pages:
                total_pages = min(total_pages, args.max_pages)
            if total_pages and start > total_pages:
                update_tag_sync_state(conn, topic_id, tag, total_pages, "completed")
                stats["skipped_done"] += 1
                continue

            payloads: list[tuple[int, dict]] = []
            if start <= 1:
                payloads.append((1, first))
            for page_no in range(max(2, start), total_pages + 1):
                time.sleep(max(0.0, delay))
                payloads.append(
                    (page_no, fetch_json(page, topic_id, tag, page_no, pause=args.pause)))

            fetched = matched = 0
            for page_no, payload in payloads:
                fetched += 1
                parsed = parse_question_html(
                    payload.get("html") or "", submitted=False, source_url="",
                    source_page=page_no, ordinal=page_no)
                question_id, how = match_question(conn, parsed, by_signature)
                if question_id is None:
                    if how == "ambiguous":
                        stats["ambiguous"] += 1
                    elif how == "no options":
                        stats["no_options"] += 1
                    # A page whose question is not stored yet is normal when
                    # the archive only holds part of the topic.
                else:
                    save_membership(
                        conn, question_id, topic_id, tag,
                        subtopic_title=sub["title"],
                        site_id=getattr(parsed, "site_question_id", None),
                        dry_run=args.dry_run)
                    matched += 1
                    stats["mapped"] += 1
                if not args.dry_run:
                    # One page = one question on this endpoint.  Committing
                    # here means a 429 on the next page costs nothing.
                    conn.commit()
                    update_tag_sync_state(conn, topic_id, tag, page_no, "running")

            if not args.dry_run:
                update_tag_sync_state(conn, topic_id, tag,
                                      total_pages or fetched, "completed")
            print(f"  tag {tag:>5} «{sub['title'][:38]}»: {fetched} page(s), "
                  f"{matched} match(es)")
        except Throttled:
            if not args.dry_run:
                update_tag_sync_state(conn, topic_id, tag, start - 1, "throttled")
            raise
        except Exception as exc:
            if not args.dry_run:
                update_tag_sync_state(conn, topic_id, tag, start - 1, "paused")
            print(f"  tag {tag:>5}: stopped — {exc}")
    return True


def row_is_on_page(conn, question_id: int, topic_id: int,
                   page_no: int) -> bool:
    """True when this stored row claims to come from that topic page."""
    row = conn.execute(
        "SELECT topic_id, source_page FROM questions WHERE id = ?",
        (int(question_id),)).fetchone()
    return bool(row) and int(row["topic_id"] or 0) == int(topic_id) \
        and int(row["source_page"] or 0) == int(page_no)


def match_question_on_page(conn, parsed, by_signature: dict[str, list[int]],
                           topic_id: int, page_no: int):
    """(question id, how), using the stored page when options collide.

    Two questions can legitimately share four one-word answers.  The page
    being read knows where it is, so a single row that claims the same
    (topic, page) is accepted; a still-ambiguous page is never guessed.
    """
    question_id, how = match_question(conn, parsed, by_signature)
    if question_id is not None or not how.startswith("ambiguous"):
        return question_id, how
    if not parsed.options:
        return question_id, how
    on_page = [qid for qid in by_signature.get(signature(parsed.options)) or []
               if row_is_on_page(conn, qid, topic_id, page_no)]
    if len(on_page) == 1:
        return on_page[0], "signature+page"
    return None, how


def fill_site_ids(conn, page, topic_id: int, args, delay: float, stats: dict) -> None:
    """Give old rows their site id by re-reading the pages they came from.

    Only whole-topic pages are walked: a row downloaded through a subtopic is
    covered by the subtopic pass.  The match is still the option signature, so
    a row whose options changed upstream is left alone instead of receiving a
    wrong id.

    Identical options are common (two questions about the same drug can share
    four one-word answers).  Instead of skipping those pages, the stored
    (topic, page) is used as a tie-breaker: the row must say it came from the
    page being read, otherwise the id is not written and the page is counted
    as ambiguous.
    """
    rows = conn.execute(
        "SELECT DISTINCT source_page FROM questions WHERE topic_id = ? "
        "AND site_question_id IS NULL AND (tag_id IS NULL OR tag_id = 0) "
        "ORDER BY source_page", (int(topic_id),)
    ).fetchall()
    pages = [int(r["source_page"] or 0) for r in rows if int(r["source_page"] or 0) > 0]
    if not pages:
        return
    by_signature = load_bank(conn, topic_id)
    print(f"\n[topic {topic_id}] site ids: {len(pages)} page(s) to re-read")
    for page_no in pages:
        payload = fetch_json(page, topic_id, None, page_no, pause=args.pause)
        parsed = parse_question_html(
            payload.get("html") or "", submitted=False, source_url="",
            source_page=page_no, ordinal=page_no)
        site_id = getattr(parsed, "site_question_id", None)
        question_id, how = match_question_on_page(
            conn, parsed, by_signature, topic_id, page_no)
        if question_id is None:
            if how.startswith("ambiguous"):
                stats["ambiguous"] += 1
            stats["id_skipped"] += 1
            continue
        if not site_id:
            stats["id_skipped"] += 1
            continue
        if not args.dry_run:
            conn.execute(
                "UPDATE questions SET site_question_id = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ? AND site_question_id IS NULL",
                (int(site_id), question_id),
            )
            conn.commit()
        stats["id_filled"] += 1
        time.sleep(max(0.0, delay))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topic", type=int, action="append", default=None,
                        help="topic id (repeatable)")
    parser.add_argument("--all", action="store_true",
                        help="every topic that has subtopics in the database")
    parser.add_argument("--site-ids", action="store_true",
                        help="fill the missing site question ids of old rows "
                             "instead of mapping subtopics")
    parser.add_argument("--delay", type=float, default=MIN_DELAY,
                        help=f"seconds between page fetches (default {MIN_DELAY}; "
                             "the site throttles bursts of this endpoint)")
    parser.add_argument("--max-pages", type=int, default=0,
                        help="stop after N pages per subtopic (0 = all)")
    parser.add_argument("--pause", type=float, default=45.0,
                        help="seconds to wait after the site starts throttling "
                             "(429 / HTML instead of JSON); default 45")
    parser.add_argument("--refetch", action="store_true",
                        help="walk subtopics that are already complete again")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the mapping without writing it")
    parser.add_argument("--db", default=None, help="database path")
    args = parser.parse_args()
    delay = max(float(args.delay), MIN_DELAY)

    conn = get_connection(args.db) if args.db else get_connection()
    init_db(conn)
    topics = args.topic or []
    if args.all or not topics:
        if args.site_ids:
            topics = [int(r[0]) for r in conn.execute(
                "SELECT DISTINCT topic_id FROM questions "
                "WHERE topic_id IS NOT NULL AND site_question_id IS NULL "
                "ORDER BY topic_id")]
        else:
            topics = [int(r[0]) for r in conn.execute(
                "SELECT DISTINCT q.topic_id FROM questions q "
                "WHERE q.topic_id IS NOT NULL AND q.topic_id > 0 "
                "ORDER BY q.topic_id"
            ) if subtopics(conn, int(r[0]))]
    if not topics:
        print("[!] no topic with subtopics found" if not args.site_ids
              else "[!] every stored question already has a site id")
        return 1

    from playwright.sync_api import sync_playwright

    # A repair/backfill rewrites stored rows; keep one copy of the archive
    # first (once per process, newest 5 kept).
    if not args.dry_run:
        from medo.backup import backup_database

        copy = backup_database(args.db or DEFAULT_DB_PATH)
        if copy:
            print(f"[i] backup: {copy.name}")

    stats = {"mapped": 0, "ambiguous": 0, "no_options": 0,
             "skipped_done": 0, "id_filled": 0, "id_skipped": 0}
    stopped_early = None
    try:
        with sync_playwright() as p:
            # Same launcher as the scraper: real Chrome, stealth defaults,
            # dead-proxy detection and the fake-DNS safety net (pinning only
            # when the direct address actually answers).
            browser = launch_browser(p, headless=True)
            context = new_context(
                browser,
                storage_state=(str(DEFAULT_STORAGE_STATE)
                               if has_storage_state() else None),
            )
            page = context.new_page()

            # Warm the session on a real page: the JSON endpoints reuse its
            # cookies/tokens. Any topic page will do.
            first = subtopics(conn, topics[0])
            try:
                page.goto(
                    f"{BASE}/qbank/topic-id-{topics[0]}"
                    + (f"?tag={first[0]['tag_id']}" if first else ""),
                    wait_until="networkidle", timeout=60000)
            except Exception as exc:
                print("[!] سایت باز نشد "
                      f"({str(exc).splitlines()[0][:120]}) — اتصال/VPN را بررسی "
                      "کن و بعد دوباره همین دکمه را بزن. چیزی از دست نرفت.")
                return 1

            for topic_id in topics:
                if args.site_ids:
                    fill_site_ids(conn, page, topic_id, args, delay, stats)
                else:
                    map_subtopics(conn, page, topic_id, args, delay, stats)
            browser.close()
    except Throttled as exc:
        stopped_early = exc
    finally:
        leftovers = []
        if not args.site_ids:
            try:
                # «Without a subtopic» = no subtopic node in its own topic,
                # which is exact even when the same question is tagged
                # elsewhere.
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
        print("    Every page written before that is saved; run again later —")
        print("    the cursor resumes at the exact page of each subtopic.")
    if args.site_ids:
        print(f"\n[done] site ids filled: {stats['id_filled']}, "
              f"skipped: {stats['id_skipped']} "
              f"(ambiguous on page: {stats['ambiguous']})")
    else:
        print(f"\n[done] mapped {stats['mapped']} question(s)"
              + (f", {stats['skipped_done']} subtopic(s) already complete"
                 if stats["skipped_done"] else "")
              + (f", {stats['ambiguous']} ambiguous page(s) skipped"
                 if stats["ambiguous"] else "")
              + (f", {stats['no_options']} page(s) without options"
                 if stats["no_options"] else ""))
    if leftovers and not args.dry_run:
        print("[i] questions still without a subtopic:")
        for row in leftovers:
            print(f"    topic {row['topic_id']}: {row['n']}")
    if args.dry_run:
        print("      (dry run — nothing was written)")
    return 1 if stopped_early else 0


if __name__ == "__main__":
    raise SystemExit(main())
