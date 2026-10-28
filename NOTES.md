# MedoOffline — current notes

One-page orientation for the next agent.  The chronological decision log (what
was tried, what failed, why) is archived in [`docs/history.md`](docs/history.md);
read this file first, then the tail of the history when a phase's reasoning
matters.  `PROMPT.md` is the full brief, `AGENTS.md` the coding rules.

## What this is

A local, offline-first mirror of medofast.ir's question bank for the owner's own
Pro account: a Python desktop app with a local web UI, a dependency-free Java
Android app, and a static Web export.  Only code is in git.  `data/` (bank,
session, assets), `logs/`, `dist/`, `tools/`, `.venv` and Android signing keys
stay local and git-ignored.

## Architecture (desktop)

| Module | Job |
| --- | --- |
| `medo/parser.py` | one saved question page → `ParsedQuestion`; strips decoys and invisible chars |
| `medo/scraper.py` | polite one-request-at-a-time walker; position-aware paging; resumable cursor |
| `medo/db.py` | SQLite (WAL) schema + queries; `questions`, `question_nodes`, `categories`, `tag_sync_state`, `micro_state`, assets |
| `medo/server.py` | `ThreadingHTTPServer` API + static UI; one job slot for scrape/repair/tag sync |
| `medo/answer_audit.py` | the resumable repair queue (answer + lesson from the Pro session) |
| `medo/backup.py` | one SQLite backup per process before a repair/backfill can rewrite rows |
| `medo/doctor.py` | proxy/DNS/TLS/login readiness verdict, Persian message |
| `medo/pacing.py` | adaptive 1.5-3 s delay; widens on 429, settles after clean pages |
| `medo/live_report.py` | one pasteable status summary from counts and `logs/medo.log` |
| `medo/share.py` / `exporter.py` / `pdf.py` | phone bundle, static web bundle, printable PDF |
| `medo/web/*` | the local UI (Persian RTL, keyboard-first practice, search) |

Jobs: scrape a topic/subtopic, discover the category tree, login, repair the
bank, map subtopics (`backfill_tags.py`), build web/PDF/phone bank, serve the
phone bundle.  The UI's «گوشی» button starts Wi-Fi transfer, builds the APK and
renders a QR code.  `scripts/dev/` holds the offline checks: `check_captures.py`,
`audit_push.py`, `clean_clone_test.py`.

Android (`android/`): no Gradle; `build.sh` uses only the local `tools/` JDK +
SDK, reads versionCode/Name from `AndroidManifest.xml`, signs with the local
keystore.  The phone bank is an index plus one chunk per category; the loader
zips/unzips with the platform's `ZipInputStream`/`ZipOutputStream` and only
materializes the chunks it needs.  `android/test.sh` runs the Java checks.

## Database invariants

- One question row per site question: identity is `site_question_id` when the
  page exposed one, otherwise `content_hash`.
- `question_nodes(question_id, topic_id, tag_id)` is the real membership table;
  a question can sit in a topic and several `?tag=` subtopics.  The legacy
  `questions.topic_id/tag_id` keep the first membership ever seen and are not
  the source of truth.
- `tag=0` is the whole-topic node.  `tag_sync_state` is the per-subtopic cursor
  (`running|paused|throttled|completed`, last page).
- Repair safety: a stored FULL explanation is never replaced by an empty,
  locked, unknown or much shorter one (log line `[audit] kept old explanation`).
  A page matched by site id may refresh stem/options display text; identity and
  `content_hash` stay unchanged (the hash input strips ZWNJ).
- FTS5 (`questions_fts`) still exists and still fires on answer/bookmark, but
  the web UI no longer searches it; it is scheduled for removal (see open work).

## Site facts (from three real captures; do not re-derive)

**F1.** `#question-meta` carries the stable site question id in
`data-pqrfhskdjf` (Pro Q53 = 1473; the same question in the free capture = 1473;
Q1 of that subtopic = 557).  Neighbours: `data-kjfhgdsa` / `data-mnofhskdjf` =
topic id, `data-mnbvcxza` = page number, `data-defhskdjfh` = option count.
`data-sasvdlfj` is a per-load token — ignore it.  The parser reads this id.

**F2.** `#block-b` holds 12 `.element-container` blocks (`data-order` 1..12) on
a Pro page; three are decoys from unrelated topics.  They are hidden only by a
rule in a dynamic stylesheet part (cid `css-…`, ~700 bytes: `display:none`,
`opacity:0`, `left:-9999px` on `.theme-<random>` classes) that is **not** in the
HTML text.  Decoy blocks have hyphenated UUID `data-element-id`; the 9 real ones
have 32-hex ids without hyphens.  Decoys are now dropped and the dropped block
text is logged for the capture check.

**F3.** Pro text contains Unicode variation selectors U+FE00..U+FE0F (161 on one
page).  `INVISIBLE_RE` in `text_utils.py` covers them, and all parser paths use
it.  A trailing «MedoFast.ir» watermark is stripped.

**F4.** Real pages contain ZWNJ (U+200C, 51 on one page).  Stored text keeps it
(the display needs it); only identity/normalisation helpers strip it.

**F5.** Lock detection must look at the explanation block, not the whole page:
«پروگنوز», «پروتئین», «پروستات», `Procalcitonin` used to mark a full 710-char
lesson as LOCKED.  The current rule is structural (status + block length), and
a free page mentioning «پروگنوز» stays FULL.

**F6.** `Harvester._position_at`: for a target it opens the page without a
`page=` parameter first; a logged-in session can restore a later position, so
the page actually shown may differ from the requested one.  The harvester
follows the shown page and stores it.  Live evidence in `logs/medo.log`:
`[position] requested page 30, site shows 1` and
`[i] Topic 78 tag 1465: requested page 1, site is at 7`.  The exact site
behaviour still needs one `medo diagnose-live` pass from the owner's network.

## Live-test status

Verified against real captures/parser fixtures: decoy removal, invisible
characters, ZWNJ, lock detection, site id parsing, option signature, node
model, bundle round-trip.  Verified in the local UI by the automated API tests
and real browser sessions on 2026-10-26/27 (download, repair, tag sync, PDF,
phone transfer).

**Not verified live since the last batch**: the repair backup/keep-old rules
against a live Pro page, `--site-ids` page tie-break against the live JSON
endpoint, and `medo diagnose-live`.  They are implemented and unit-tested; run
one `medo live-report` (paste it into a chat) plus a small repair on a known
topic before trusting them on the whole bank.

## Open work (next)

1. Live pass (owner network, VPN direct for medofast.ir):
   `medo doctor`, `medo diagnose-live --topic 15 --tag 1473`, one small repair,
   `scripts/backfill_tags.py --site-ids --topic 78`.  Paste `medo live-report`.
2. After that pass succeeds: drop FTS5 (`questions_fts` table, its triggers and
   the tests) with a migration; the UI already ignores it.
3. Synthetic 20k-question stress test (`scripts/dev/make_big_bank.py`), web and
   Android load/heap; add lazy chunk hydration only if a device exceeds a safe
   heap.
4. Re-run `python scripts/dev/clean_clone_test.py` after every batch; it runs
   `audit_push.py` and pytest on an export of HEAD.

## Commands

```bash
.venv/Scripts/python -m pytest -q                 # full suite
bash android/test.sh                             # Java checks
node --check medo/web/app.js                     # UI syntax
.venv/Scripts/python scripts/dev/clean_clone_test.py
.venv/Scripts/python -m medo.cli live-report --no-network
.venv/Scripts/python -m medo.cli diagnose-tags
```

`pytest` generates the synthetic `tests/fixtures/before|after.mhtml` once via
`scripts/make_fixtures.py`; real captures in `data/diag/` are never committed.
