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
keystore.  The phone bank is a light index plus one chunk per category; `Bank`
hydrates a chunk when a question opens and keeps only the newest two (LRU 2),
so a 20k archive starts at ~26 MB settled instead of loading every lesson.
`android/test.sh` runs the Java checks (189).

The home screen has two trees, the same meaning as the desktop tabs: «بانک من»
(`Bank.flattenStored` — only branches that hold a question) and «همهٔ دسته‌ها»
(the full site tree).  The mode is remembered in `SharedPreferences`.

Appearance lives in `Theme` (five palettes: dark, AMOLED, light, sepia,
blue-grey) and `Ui.apply*`; screens read `Ui.*` tokens only and never a literal
colour, and `SettingsActivity` (gear on the home screen) writes the theme and
the four text sizes.  A change applies at once (`Ui.apply` on resume) without
restarting the app.  `android/test.sh` proves every palette defines every token
and keeps text contrast >= 4.5.

Lessons are prepared by `LessonText` before `Html.fromHtml`: table rows become
lines with «·» between cells, lists get bullets, an image becomes «تصویر», and
a run with no space gets a zero-width break every 28 characters, so a 30k
character lesson wraps inside the scroll view instead of forming one clipped
block.  The web equivalent is `.explanation-body` in `medo/web/style.css`
(`overflow-wrap`, scrolling tables/pre, `img { height:auto }`).

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

**Not verified on a phone at all**: no device or emulator is attached to this
machine, so the «بانک من» tab, the five themes and the long-lesson layout are
checked by the JVM suite and a headless-Chrome run (`scripts/dev/
check_long_lesson.py` at 500 px and 1280 px) only.  The APK builds and signs
(`android/build.sh`), but nobody has looked at it on a screen yet.

## Stress numbers (20k synthetic, 2026-10-28)

Generated by `scripts/dev/make_big_bank.py` (103 MB db, 4.1 MB bundle, 40
chunks) and measured by `scripts/dev/bench_bank.py` on this machine:

- web: 51 sequential `/api/questions` pages = 36.8 MB / ~20 s; first usable
  screen ~26 s; search «واژه123» ~0.4 s; Chrome heap ~80 MB.
- Android JVM, light index + LRU 2: `-Xmx128m` loads 20k in ~1.1 s, peak
  ~93 MB, settled ~26 MB; `-Xmx64m` still fails while parsing the index itself.
  Before lazy hydration the same bundle ran out of memory at 128 MB.
- No emulator or device is attached on this machine; the JVM numbers are a
  proxy.  On a phone use `adb shell dumpsys meminfo ir.medo.app`.

## Open work (next)

1. Live pass (owner network, VPN direct for medofast.ir):
   `medo doctor`, `medo diagnose-live --topic 15 --tag 1473`, one small repair,
   `scripts/backfill_tags.py --site-ids --topic 78`.  Paste `medo live-report`.
2. After that pass succeeds: drop FTS5 (`questions_fts` table, its triggers and
   the tests) with a migration; the UI already ignores it.
3. Device test of the lazy bank: install the APK + 20k bundle, open a topic,
   quiz one question, browse categories, export one section's PDF, then read
   `adb shell dumpsys meminfo ir.medo.app`.  A bulk whole-bank PDF deliberately
   hydrates everything (documented in `Bank.fullQuestions`), so keep it out of
   the heap test.
4. Device pass for the appearance work: switch all five themes and all four
   text sizes, then read the question, options, correct/wrong highlight and a
   long explanation; screenshot each theme.  Open a lesson with a wide table
   and a 500-character token and confirm the whole text scrolls.
5. Web list load is ~26 s at 20k (36.8 MB of JSON); if that size becomes real,
   trim the light-row payload or serve gzip before touching the phone path.
6. Re-run `python scripts/dev/clean_clone_test.py` after every batch; it runs
   `audit_push.py` and pytest on an export of HEAD.
7. `scripts/dev/make_long_lesson.py` + `check_long_lesson.py` build the 48k
   character synthetic lesson and screenshot it in headless Chrome; keep them
   as the regression check for the web lesson layout.

## Commands

```bash
.venv/Scripts/python -m pytest -q                 # full suite
bash android/test.sh [bundle.medo]               # Java checks
node --check medo/web/app.js                     # UI syntax
.venv/Scripts/python scripts/dev/clean_clone_test.py
.venv/Scripts/python -m medo.cli live-report --no-network
.venv/Scripts/python -m medo.cli diagnose-tags
.venv/Scripts/python scripts/dev/make_big_bank.py --db build/stress/medo.db --reset
.venv/Scripts/python scripts/dev/bench_bank.py
```

`pytest` generates the synthetic `tests/fixtures/before|after.mhtml` once via
`scripts/make_fixtures.py`; real captures in `data/diag/` are never committed.

A clean venv install was verified on 2026-10-28 (`python -m venv
build/fresh-venv`, `pip install -e ".[dev]"`, then `pytest -q` → 300 passed).
pip prints `typer 0.27.3 does not provide the extra 'all'`; the app still works
because Rich is a direct dependency — drop `[all]` from `pyproject.toml` next
time that line is touched.
