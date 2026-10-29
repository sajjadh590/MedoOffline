# Handoff report — MedoOffline, batch A1–B5 (safety fixes + owner requests)

Date: 2026-10-29 · Branch: `master` · Working tree clean after this commit.

Short technical report for another agent. Long-form context is in
[`PROMPT.md`](PROMPT.md); the chronological log is [`docs/history.md`](docs/history.md);
the current state page is [`NOTES.md`](NOTES.md) — read NOTES first.

---

## 1. What was asked

Two parts: (A) fix what a review found in the previous round — repair-queue
correctness, the lesson keep-rule, a git-history audit, more `live-report`
detail — and (B) owner requests: an Android «بانک من» tab, an Android settings
screen with five themes and four text sizes, faster-but-safe extraction, a
huge-explanation layout fix (web + Android), and a real progress/Stop story for
the subtopic organizer. Rules: one request at a time, never bypass quota or a
limit, stop on 429/captcha/limit, no scraped or paid content in git, tests with
every item, NOTES under 200 lines.

## 2. Commits (newest first)

| Commit | Scope |
| --- | --- |
| `e4873df` | B4 web — lesson layout + `make_long_lesson.py` / `check_long_lesson.py` |
| `0e8c199` | B1 + B2 + B4-Android — bank tab, themes/settings, `LessonText` |
| `2c82441` | B5 — organizer job record, progress, Stop, watchdog, CLI output |
| `721a0af` | B3 — learned pace ceiling, cool-down, redundancy removal, live stats |
| `0078d7a` | A4 — site-id gaps, repair reasons, recent 429 delays in `live-report` |
| `200d6db` | A3 — `audit_push.py --history` over every reachable commit |
| `d96b7b9` | A1 + A2 — repair queue reasons/retry-after, normalized keep-rule |

## 3. Changes, by area

### A1/A2 — repair safety (`medo/answer_audit.py`, `medo/db.py`)
`questions` gained `repair_reason` and `repair_retry_after`; a row keeps its
reason and stays queued until a real repair succeeds. LOCKED / UNKNOWN / empty /
quota-limited / rate-limited pages never mark a row repaired, and a daily-limit
message stops the whole pass with the reason recorded. The length rule now works
on normalized text: U+FE00..FE0F removed, trailing «MedoFast.ir» watermark
stripped, decoy blocks (hyphenated-UUID `data-element-id`) dropped, whitespace
collapsed; both normalized lengths are logged, the old row is kept with
`[audit] kept old explanation`, and a clean FULL lesson always beats a dirty
one.

### A3 — history audit (`scripts/dev/audit_push.py --history`)
Walks `git rev-list --objects --all` with the same rules as the working-tree
audit (sessions, `*.db`, `*.mhtml`, keystore, >1 MB files, long Persian
paragraphs, text copied from `data/diag`) and prints path + commit + reason
only. It never rewrites history. Allowlisted binaries are named explicitly:
`android/libs/zxing-core.jar`, the five Vazirmatn `.woff2` files and the three
PWA icons.

### A4 — `medo live-report`
Adds: rows with `source_page = 0` (they can never get a site id), rows still
without a site id grouped by cause, the repair queue grouped by reason, and the
429 events of the last seven days with timestamp and the delay that was in
force.

### B1 — Android «بانک من» (`Bank.flattenStored`, `MainActivity`)
The home screen now has the desktop tree tabs. «بانک من» keeps only branches
that hold at least one downloaded question (ancestors of a filled subtopic
stay); «همهٔ دسته‌ها» shows the full site tree; the mode is remembered in
`SharedPreferences`. No bundle format change was needed: every question already
carries its `nodes`, so v1/v2 banks import unchanged and QR/Wi-Fi transfer
carries the same data.

### B2 — themes and text size (`Theme.java`, `Ui.java`, `SettingsActivity`)
Five palettes (dark, AMOLED, light, sepia, blue-grey) × 20 semantic tokens.
`Ui` now holds the active tokens and the four text sizes; every screen reads
tokens only (camera screens keep their own dark constants, PDF keeps its print
palette on white). A change is saved, applied on resume without restart, and the
quiz view is rebuilt in place so the reader stays on the same question. The JVM
suite proves every palette defines every token and that text keeps ≥ 4.5:1
contrast on every surface it is drawn on.

### B3 — faster, still polite (`medo/pacing.py`, `medo/scraper.py`)
One request at a time is untouched. Speed comes from removing waste (no page-1
reload after a position drift) and from learning: −10 % after 25 consecutive
clean pages, floor 1.0 s, jitter kept; a 429 doubles the delay, records the
failing value and sets a persistent ceiling of last-good × 1.25 that the next
run starts from, with a visible cool-down instead of an immediate retry.
Requests/minute, delay, today's 429s, questions/hour and ETA appear in the UI
dock, the API and the CLI. A fake server that rejects anything faster than 2 s
is used in tests to prove the pacer converges and never loops on 429.

### B4 — long explanation layout
Web: measured root cause — the lesson box hid its overflow, so one wide table or
a 500-character token stretched `.explanation-body` to ~3900 px and the rest was
clipped. The body now wraps anywhere, children stay inside the column, tables
and `pre` scroll horizontally, images scale by width. `scripts/dev/make_long_lesson.py`
builds a 48k-character lesson and `check_long_lesson.py` measures it in headless
Chrome at 500 px and 1280 px (no horizontal overflow at either width).
Android: `LessonText` prepares the stored HTML before `Html.fromHtml` — one line
per table row with «·» between cells, list bullets, an image placeholder, and a
zero-width break inside any run longer than 28 characters — so the text can
always wrap. The JSON-suite covers a 30k-character lesson.

### B5 — organizer with real progress (`medo/jobs.py`, server, UI, CLI)
The job writes a database record per page: state, processed/total, current
topic/page, items per minute, ETA, the last twenty log lines and a heartbeat.
The UI polls it (survives reload), Stop is honoured at the next committed page
with a hard-stop fallback, a 60-second silence is flagged with the last log
line, and `medo organize` prints the same page-by-page output. Every page was
already committed, so a stopped run resumes from its own cursor.

## 4. Verification (actual runs)

| Check | Result |
| --- | --- |
| `pytest -q` (working tree) | **321 passed** (102.8 s) |
| `scripts/dev/clean_clone_test.py` (`git archive HEAD`) | audit **0 findings / 141 tracked files**, pytest **309 passed, 12 skipped** |
| `bash android/test.sh` | **ALL PASSED (189 checks)**, three consecutive runs |
| `bash android/build.sh` | signed `dist/medo.apk`, `versionCode 4`, `1.2`, 288 KB |
| `scripts/dev/check_long_lesson.py` | `PASS: no horizontal overflow at 500px, 1280px` |

One pre-existing flaky check was corrected rather than weakened: `random picks
answerable questions` now hydrates the picked id (`bank.question(id)`) before
asserting on its options, because a light index entry carries no options by
design.

## 5. NOT VERIFIED (say this out loud)

1. **Nothing has run against medofast.ir.** The live repair rules (A1/A2), the
   `--site-ids` page tie-break and the learned pace (B3) are unit-tested only.
2. **No Android device or emulator is attached.** The «بانک من» tab, the five
   themes, the four text sizes and the long-lesson text are covered by the JVM
   suite and a compile+dex+sign build; nobody has looked at a screen yet.
3. **The Android half of B4 is a different fix from the web half.** The clipping
   was reproduced and fixed on the web (measured). On Android the screen already
   scrolled and had no fixed heights, so the two defects found by reading the
   code (table cells merging, an unbreakable token) were fixed and unit-tested —
   the original on-device symptom was not reproduced.
4. The web lesson check uses a synthetic lesson at two widths, not a real stored
   lesson in a real phone browser; theme contrast is computed from token values,
   not measured on a rendered screen.

## 6. Commands for the live pass (in order)

```bash
.venv/Scripts/python -m medo.cli doctor
.venv/Scripts/python -m medo.cli live-report --no-network      # paste this
.venv/Scripts/python -m medo.cli diagnose-live --topic 15 --tag 1473
.venv/Scripts/python -m medo.cli serve                         # «سلامت بانک» → repair one known topic
.venv/Scripts/python scripts/backfill_tags.py --site-ids --topic 78 --max-pages 20 --dry-run
.venv/Scripts/python -m medo.cli organize --topic 78 --max-pages 20
bash android/build.sh && tools/sdk/platform-tools/adb.exe install -r dist/medo.apk
```

## 7. Do not repeat these dead ends

- Treating a LOCKED/empty/quota page as a successful repair, or comparing
  lesson lengths without normalizing first (decoys and the watermark alone used
  to make every comparison wrong).
- Making speed come from parallelism, proxies, extra accounts or forged
  cookies — the only accepted wins are removing redundant requests and learning
  the safe delay.
- Fixing a layout by hiding overflow: the lesson container must wrap and scroll,
  never clip.
- Splitting B1/B2/B4-Android into separate commits — they share `MainActivity`,
  `QuizActivity` and `Ui`; a split leaves commits that do not compile.
