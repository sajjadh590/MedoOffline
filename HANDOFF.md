# Handoff report — MedoOffline, phases 0–6

Date: 2026-10-28 · Branch: `master` · Working tree clean · Nothing pushed.

This is the short technical report for another agent. The long-form context
(product, every past bug, dead ends) is in [`PROMPT.md`](PROMPT.md); the
chronological evidence is in [`NOTES.md`](NOTES.md). Read those two before
changing behaviour.

---

## 1. What was asked

Finish the remaining phases of the project plan (Phase 4 → 6) and leave a
report. Phases 0–3 had already landed earlier (capture diagnostics, Pro lesson
parsing, stable site ids + many-to-many nodes, subtopic positioning/backfill).

## 2. Commits

| Commit | Scope |
| --- | --- |
| `2844e1b` | Phase 4 — repair queue, doctor, adaptive pacing, job lock, PDF on demand |
| `631cfdf` | Phase 5a — shared Persian search, light/paged API, FTS trigger, Android build, keystore backup |
| `7b440af` | Phase 5b — `.medo` v2 (zip index + category chunks), phone word index, category/image selection |
| `2ccda3e` | Phase 6 — clean-clone tests, housekeeping, README from `--help`, PROMPT/NOTES refresh |

`git log --oneline -8` also shows the earlier phase commits (`37ca873`,
`4403cbe`, `95d2be7`, `5540fe6`, …).

## 3. Changes, by area

### 3.1 Repair only what the login can improve (Phase 4)
- `questions.fetched_as` (`guest` / `login` / `unknown`) is written on every
  upsert from the active session. Migration runs in `init_db`; existing rows
  default to `unknown`, so every pre-fix row is treated as repair-worthy exactly
  once.
- `NEEDS_REPAIR_SQL`, `count_needing_repair`, `questions_needing_repair` in
  `medo/db.py`.
- `medo/answer_audit.py` is now one resumable job: queue by id, locate a page by
  `site_question_id` or unique option signature, submit «نمایش جواب» with the
  same `Harvester` path, store answer/votes/lesson, download the mind-map via
  `Harvester._store_assets`, commit per question. Stops on the site's daily
  limit with `status="limit"` and reports the remaining queue.
- Server: repair refuses to start without a saved session; result carries
  `remaining`; `/api/bank/health` exposes `needs_repair`.
- Fixed while testing: `answer_audit.py` used `time.sleep` without importing
  `time` (only reachable once the queue actually advanced).

### 3.2 Pre-flight, pacing, locking (Phase 4)
- `medo/doctor.py` + `medo doctor` + `GET /api/doctor`: Windows dead proxy,
  fake-IP DNS vs direct reachability, TLS to `medofast.ir`, saved session, and
  (`--deep`) a live check that `#question-meta` still exposes the question id.
  One Persian verdict (`ready` / `auth` / `network`) and the VPN bypass-rule
  help text.
- Scrape and every network repair are gated by `doctor.quick_gate`; local PDF
  rendering is **not** gated.
- `medo/pacing.py` (`AdaptiveDelay`): 1.5–3 s start, ×2 per 429 up to 12 s,
  `Retry-After` honoured (5–300 s), −10 % after 40 clean pages, floor 1.2 s.
  `Harvester.pace` owns the range; wired at question save and throttle.
- One `threading.Lock` guards the single job slot. `_begin_repair` now returns
  `(bool, reason)`; `_take_slot` is the shared primitive (PDF uses it directly).
- The full PDF is no longer rebuilt after every run: `POST /api/pdf` +
  «ساخت PDF کتاب» builds the selected category (or the whole bank) on demand.

### 3.3 Search, API, lesson rendering (Phase 5a)
- One canonical search form:
  `medo/text_utils.normalize_search_text`, `medo/web/normalize.js` (works as a
  `<script>` and as a `require`-able module), `android/.../Text.java`.
  Contract: `tests/search_vectors.json` (9 cases), asserted by Python, Node
  (when installed) and the Java test harness.
  Rules: Arabic ي/ى/ك → Persian, ZWNJ + invisible selectors removed, Persian and
  Arabic-Indic digits → ASCII, whitespace collapsed, case folded.
- `/api/questions` returns light rows (no `stem_html` / `explanation_html`) with
  `offset`/`limit`; `/api/question/<id>` returns the full body. The UI loads
  400-row pages, folds each question's search text once at load, debounces the
  search 200 ms, and fetches a full body only when a lesson is opened.
- FTS trigger narrowed to `AFTER UPDATE OF stem_text, explanation_text,
  options_json`, so answer/bookmark clicks no longer re-index.
- Pro lessons read as a document: `h4/h5/h6`, `ul/ol/li`, `mark`, `blockquote`
  and the «تایید گزینه N» badge in `style.css` and `templates/book.html` (Jinja
  filter `key_badge` for the PDF).
- Android build from a clean clone: blanket `*.xml` ignore replaced by
  `/coverage.xml`; `android/AndroidManifest.xml` + `android/res/**` tracked;
  test asserts `git ls-files android` contains them. `build.sh` reads
  versionCode/Name from the manifest and bumps the code only after a signed APK
  exists; a missing keystore fails with a Persian message instead of generating
  a new identity.
- `medo keystore-backup`: zip + SHA-256 into `dist/keystore-backup/`.

### 3.4 Phone bank, v2 (Phase 5b)
- `.medo` is now a ZIP: `index.json` (format/version, stats, tree, one light
  entry per question with `chunk` id, chunk manifest) plus
  `chunks/<root-category>.json` with full lesson bodies. Legacy single-JSON
  files still load (`Bank.load` sniffs `PK`).
- Android `Bank.parseZip` reads the index first, then each chunk straight from
  the archive and replaces the light entry by id — one chunk resident at a time
  instead of the whole file as `byte[]`, `String` and parsed tree.
- `Bank.searchIds` builds a word → ids index lazily; `BrowseActivity` computes
  the hit set once per query instead of folding every question per keystroke.
- `medo build-bank --category/-c` (repeatable) ships selected categories;
  `--images/--no-images` embeds small WebP mind-maps as data URIs.
- Share server serves the zip with `application/zip`; `read_bundle()` can undo
  the format in Python (tests/inspection).

### 3.5 Test hygiene and docs (Phase 6)
- `tests/conftest.py`: session fixture runs `scripts/make_fixtures.py` when the
  synthetic MHTML pair is absent (real captures are never generated; tests that
  need them skip).
- `pytest` + `pytest-asyncio` moved to `[project.optional-dependencies].dev`;
  unused `aiofiles` removed.
- Deleted `.github/workflows/scrape.yml` (scrape output must never be an
  artifact), `_patch/`, `inspect_shortcut.ps1`; `launcher.log` untracked and
  `*.log` ignored; a 1.2 MB junk file under `tests/fixtures` removed.
- `README.md` rewritten from the real `--help` of every command; a test fails if
  it documents a flag the CLI does not define.
- `PROMPT.md` §8/§7.7/§11 updated (current state, new bug table with the
  approaches that failed, file map + open work).

## 4. Verification (actual runs)

| Check | Result |
| --- | --- |
| `pytest -q` (working tree) | **283 passed** |
| Clean clone via `git archive HEAD`, existing venv | **272 passed, 11 skipped, 0 failed** |
| `bash android/test.sh` | **ALL PASSED (140 checks)** — zip load, word search, normalization vectors, QR stride, answer keys |
| `bash android/build.sh` | APK signed (280 KB), versionCode from manifest (2 → bumped to 3) |
| `node --check medo/web/app.js` / `normalize.js` | clean |
| `python -m compileall -q medo` | clean |
| `medo build-bank` | zip bank 0.3 MB from the local DB; read back by the Java harness |
| Live UI probe (`serve --port 8099`, Chromium) | 516 questions load and render; search `بيمار` (Arabic yeh) → 315 hits; clicking an option → `POST /api/answer` 200, then `GET /api/question/106` 200 and a 1,449-char lesson renders; no console errors |

The clean-clone run was done by exporting `HEAD` to `build/clean-clone` and
running the repo's venv against it — not a brand-new `pip install` (no network
for one was assumed). It proves the fixture generation and the repo being
self-contained, not the dependency resolver.

## 5. Decisions and deliberate deviations

1. **`.medo` chunks are streamed at load, not lazily hydrated with an LRU of 2.**
   `Question` is immutable and every screen iterates `bank.questions`; making
   bodies defer would have required replacing objects across the UI. The memory
   bug that mattered (three copies of the whole file) is fixed: peak is one
   chunk plus the question objects. The upgrade path is documented in PROMPT §11
   (the `positions` map is already there).
2. **FTS5 kept, trigger narrowed.** The browser now searches its own normalized
   index; FTS is only used by tests. PROMPT §11 records the two honest options
   (wire it or delete it) rather than leaving it ambiguous.
3. **`_begin_repair` signature changed to `(bool, str)`.** Callers now show the
   real refusal reason (network verdict / other job) instead of a generic one.
4. **No live-site claim.** The machine cannot reach `medofast.ir` reliably, so
   `doctor`, live repair, live tag sync and a real login are implemented,
   unit-tested and log their evidence — but were **not** exercised end-to-end
   against production in this session.

## 6. Open work (ordered)

1. Live proof with VPN routed direct: `medo doctor --deep` → small scrape →
   `diagnose-tags` → one repair run; record in `NOTES.md`.
2. Lazy per-chunk hydration + LRU 2 if a real bank outgrows phone RAM.
3. Decide FTS5: wire `/api/questions?q=` or delete table + triggers + tests.
4. Device test of the fresh APK and zip bank (QR import, browse, quiz, PDF,
   search).
5. Verify `build-bank --images` on a device for lesson-open latency.

## 7. Do not repeat these dead ends

- Filtering Pro decoy blocks by `data-order`, block count, or HTML text —
  visibility lives in a dynamic stylesheet; mark hidden nodes in the live page
  and skip hyphenated-UUID blocks.
- Deciding LOCKED from substrings anywhere in the lesson («پروگنوز» is not Pro).
- Trusting the site's remembered position for a subtopic page 1; request the
  page explicitly and verify the counter, else `paused`.
- Committing tag mapping only after all subtopics, or matching ambiguous option
  signatures to several questions.
- Shipping the whole bank as one `SELECT *` JSON, or re-indexing FTS on every
  answer click.
- `*.xml` in `.gitignore`, hardcoded `--version-code 1`, generating a new
  keystore on a missing key.
- Loading the phone bank as `byte[]` → `String` → tree in one go.
