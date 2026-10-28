# PROMPT.md — MedoOffline: full project brief for a new AI agent

> **How to use this file.** Paste this whole document as the system/context prompt for a
> fresh AI working on this repository. It explains *what* the product is, *what already
> exists* (desktop + Android), *what the current state is*, and — most importantly —
> *every significant bug we hit, how we fixed it, and which attempted fixes did NOT work*
> so you do not repeat dead ends. Read it before writing any code.
> Companion files: [`NOTES.md`](NOTES.md) = the long chronological decision log (read it
> when you need evidence), [`AGENTS.md`](AGENTS.md) = the coding philosophy this repo
> follows (smallest complete change, lazy senior dev), [`docs/design-contract.md`](docs/design-contract.md)
> = UI/UX rules.

---

## 1. What this project is

**MedoOffline** is a local, offline-first question-bank archiver for the Persian medical
Q&A site **medofast.ir** (مدوفست). It scrapes multiple-choice questions (with answers,
vote percentages and explanations) into a local SQLite archive, then lets the user study
them **completely offline** on:

1. **Desktop** — a local Python web app (browser UI at `127.0.0.1`) + a Typer CLI.
2. **Android** — a dependency-free Java app (`dist/medo.apk`, ~276 KB) that reads a
   transferred `.medo` bank file and works with **no internet at all**.

Repository: `https://github.com/sajjadh590/MedoOffline.git` (branch `master`).
Project root: `E:\MedoOffline` (Windows, Git Bash available).

### Design rules that are non-negotiable
- **Politeness toward the source site**: 1 request at a time, random delay 1.2–3 s,
  exponential backoff, stop on 429/captcha. **Never** bypass the server-side daily guest
  quota with proxies or forged cookies — the answer is "log in with a free account".
- **Scraped content is never committed.** `data/`, `logs/`, `dist/`, `tests/fixtures/*.html|mhtml`
  are gitignored. Only code ships (last push: 93 code files; heavy `tools/` JDK+SDK 446 MB
  and `.venv` 145 MB are ignored).
- The app is **offline**: the web bundle has no CDN links, fonts are bundled, `/api/*` is
  network-only, media is content-addressed by SHA-1.
- UI language is **Persian (RTL)**, Vazirmatn font, Persian numerals in the UI.

---

## 2. Tech stack

| Layer | Choice |
|---|---|
| Language (desktop) | Python 3, stdlib-first (`http.server`, `sqlite3`, `html.parser`, `unicodedata`) |
| Third-party deps | Playwright (sync API), Typer, Jinja2, Pillow (see `pyproject.toml`) |
| Browser control | Playwright driving the **system Chrome/Edge** (never downloads Chromium) |
| DB | SQLite in WAL mode + FTS5 Persian full-text search |
| Web UI | Vanilla JS + CSS, no framework, no build step, works from `file://` too |
| PDF (desktop) | Jinja2 template `medo/templates/book.html` → Chromium `page.pdf()` |
| Android | Plain Java (minSdk 21, targetSdk 30), **no Gradle, no AndroidX**, hand-built with `aapt2`/`javac`/`d8` from local `tools/` |
| Android QR | `zxing-core 3.5.3` bundled offline at `android/libs/zxing-core.jar` |
| Tests | `pytest` (Python) + `bash android/test.sh` (JVM, no emulator) |

---

## 3. Repository layout

```
E:\MedoOffline\
├── PROMPT.md              # this file
├── NOTES.md               # chronological decision log (the "why" archive)
├── AGENTS.md              # coding philosophy for agents working here
├── docs/design-contract.md
├── launcher.py            # starts server + opens browser (desktop shortcut target)
├── pyproject.toml
├── medo/                  # Python package (the desktop app)
│   ├── paths.py           # single source of truth for data/ dist/ logs/ roots
│   ├── parser.py          # obfuscation-resistant question parser
│   ├── html_utils.py      # HTML sanitizer (default-deny, XSS-hardened)
│   ├── text_utils.py      # invisible-Unicode stripper + Persian normalization
│   ├── models.py          # dataclasses/enums (ParsedQuestion, AnswerSource…)
│   ├── db.py              # schema, migrations, tree helpers, progress, dedup
│   ├── browser.py         # system-Chrome discovery + stealth context factory
│   ├── netfix.py          # DNS-fake-IP / proxy problem diagnosis (VPN friendly)
│   ├── auth.py            # interactive login + login-signal detection (Chromium)
│   ├── scraper.py         # Harvester (scraping), discover_hierarchy, backfill
│   ├── answer_audit.py    # re-read pages to recover missing answers ("سلامت بانک")
│   ├── assets.py          # image dedup + WebP optimization
│   ├── pdf.py             # desktop PDF book (TOC, per-section answer keys)
│   ├── exporter.py        # static web bundle builder (dist/)
│   ├── share.py           # read-only Wi-Fi server + .medo bank builder
│   ├── server.py          # local HTTP server, JSON API, PhoneBridge, task controller
│   ├── applog.py          # durable rotating file log logs/medo.log + stdout tee
│   ├── cli.py             # Typer CLI
│   ├── templates/book.html
│   └── web/               # index.html, app.js (~2600 lines), style.css, sw.js,
│                          # fonts/, icons/, vendor/qrcode.js
├── scripts/               # backfill_tags.py + one-off probe_*.py scripts
├── tests/                 # pytest suite (226 tests)
├── android/               # the Android app (source + build/test scripts)
│   ├── build.sh → dist/medo.apk ; test.sh → JVM checks
│   ├── AndroidManifest.xml (v1.2), libs/zxing-core.jar, res/
│   └── src/ir/medo/app/   # MainActivity, BrowseActivity, QuizActivity, ScanActivity,
│                          # Bank, BankStore, Question, Quiz, PdfExporter, QrDecoder,
│                          # ScanTarget, SharedFileProvider, StateStore, Ui, Node, Io, MiniJson
├── dist/                  # generated output (gitignored): web bundle, PDF, medo.medo, medo.apk
├── data/                  # medo.db, storage_state.json, assets/ (gitignored)
├── logs/medo.log          # durable log (gitignored)
└── tools/                 # local JDK8 + Android SDK (~446 MB, gitignored)
```

---

## 4. How the scraping actually works (the hard-won knowledge)

The site fights scrapers. Everything below was proven by live probes, not guessed.

### 4.1 Page/DOM facts
- Question pages: `/qbank/topic-id-<N>`, one question per page, paginated through
  `button[data-nav-role="next"][data-page="N"]`.
- **All CSS class names are randomized per page load.** Never select by class; anchor on
  stable **ids**: `#qtxt` / `#questions-container` (stem), `#label1..#label5` (options),
  `#prograssBar{i}` (vote bars — note the double-s typo), `#block-b` (answer + explanation),
  `#image-container`.
- **Real pages have no `#q-stem-container`** — that id exists only in our synthetic
  fixtures. The real stem is found by evaluating the page's obfuscation `<style>` rules
  (`getComputedStyle` visibility/display/box) and taking the visible candidate among
  random-class sibling blocks; decoy blocks contain glued text of *other* questions.
- The site injects **zero-width characters** (U+200B–200F, U+2060–2064, U+FEFF) inside
  words. Always strip before comparing/storing.
- Option truth comes from base64: `span.encoded-option[data-encoded-text]` and
  `#prograssBar{i}[data-encoded-percent]`.
- **Answer detection (colour is the authority)**: marker regex `تایید\s*گزینه\s*(\d)` in
  `#block-b` works *before* submitting; after submitting, correct label colour is
  `rgb(48,127,226)`, wrong is `rgb(239,64,86)`. Record `answer_source = marker|color|both|unknown`.
- The stem stays encrypted until the site decrypts it (`«در حال پردازش متن سوال...»`);
  the scraper must wait (`DECRYPT_EXTRA_ATTEMPTS`) instead of storing junk.
- Site chrome that must never become a "question": the «سابقة عملکرد شما» stats widget,
  «پنل کاربری» nav labels, the SMS-login page, browser-check pages.

### 4.2 Real site hierarchy (crawl order)
```
/main-menu  →  /qbank , /qbank-keshvari , /qbank-talifi      (entry landings)
            →  /qbank/cat-id-N/                              (7 categories)
            →  <a href="/lobey/L"> lesson cards              (lessons, id in data-topic-id)
            →  lesson page: «شروع مطالعه جامع» button has onclick="…/qbank/topic-id-N"
            →  /qbank/topic-id-N?tag=T                       (micro-topics «زیرمبحث», with counts)
```
- `/qbank/cat-id-N/by-year/` answers **502** — never crawl it.
- `/main-menu` alone contains **zero** topic links (this bug cost us a whole session).
- Current tree in the archive: **7 categories, 150 topics, 2,435 micro-topics (~2,592 nodes)**.
- Root order is custom (`db.root_rank()`): **دستیاری پزشکی → پره انترنی → تالیفی → rest**
  (desktop web UI and the phone bank both use it).

### 4.3 Anti-bot / environment realities (diagnosed, not guessed)
- Headless Playwright with default UA → **403** «مرورگر شما پشتیبانی نمی‌شود». Fix in
  `medo/browser.py`: real Chrome UA from `browser.version`, `--disable-blink-features=AutomationControlled`,
  no `--enable-automation`, `locale=fa-IR`, `timezone=Asia/Tehran`, init script hiding
  `navigator.webdriver/plugins/languages/chrome` + permissions shim. One `new_context()`
  factory is used by scraper, probes, login window, discovery.
- WAF returns **502** for bare `curl` UA bursts → use a Chrome UA (Playwright unaffected).
- The machine's VPN/tunnel can resolve `medofast.ir` to a **fake IP 198.18.0.94**
  (198.18.0.0/15) → `netfix.py` resolves the real A record over DoH and pins it with
  `--host-resolver-rules`… **but only when the system path actually fails** (see §7).
- Windows registry sometimes has `ProxyEnable=1, ProxyServer=127.0.0.1:10808` with
  **nothing listening** → the site itself renders «اینترنت قطع است». `netfix.proxy_problem()`
  detects it, logs a clear Persian message, and the browser is launched with
  `--no-proxy-server`. The user must turn the VPN on or disable the proxy; the code cannot fix it.
- The guest daily quota is server-side (IP/account). When exhausted the site redirects with
  `?ddos_redirect=true` → we report `limit` ("guest quota exhausted"), **not** `auth_required`,
  and auto-resume after login.
- This dev machine frequently **cannot reach medofast.ir at all** (curl → 000/RESET), so
  most live verification is blocked; the offline test suite is the safety net.

---

## 5. Desktop app — feature inventory

### 5.1 Server (`medo/server.py`, ~1670 lines)
Local HTTP server (bind `127.0.0.1`, Host-header allowlist). JSON API:

| Endpoint | Purpose |
|---|---|
| `GET /api/stats` | archive statistics |
| `GET /api/questions` | questions (filtered) |
| `GET /api/tree` | decorated category tree with per-node progress |
| `GET/POST /api/scrape/start` | start a download (`topic_id`, `tag_id`, `targets[]`, `max_pages`, resume) |
| `POST /api/scrape/stop` \| `/api/scrape/status` \| `/api/scrape/state` \| `/api/scrape/resume` | control/inspect; `status` includes `usage` (bytes today, avg/estimated per question), `can_resume`, `pending` |
| `GET /api/discover-tree` | one-time BFS crawl of the site hierarchy |
| `GET /api/login`, `GET /api/auth-status` | interactive browser login + status polling (`authorizing` while the window is open) |
| `POST /api/answer`, `POST /api/bookmark` | persisted answers/bookmarks (validated, idempotent) |
| `GET /api/bank/health` | bank health: total / missing answers / missing subtopics |
| `POST /api/answers/audit` | run `answer_audit` recovery |
| `POST /api/tags/sync` | run `backfill_tags` **with the saved session** |
| `GET /api/phone` + `/api/phone/start|stop|bundle|build-apk|apk|bank` | **PhoneBridge**: Wi-Fi transfer server, LAN address list with QR, APK download, in-app APK rebuild with log |
| `GET /api/export-web` | rebuild the static bundle |

Security hardening already in place: Host-header check (DNS rebinding), path-traversal
block on `/assets/`, all handler exceptions converted to Persian JSON errors, `Cache-Control: no-store`
+ `?b=<mtime>` asset stamping (a patched UI can never be masked by cache).

`ScraperTaskController` runs downloads in a worker thread, pushes every message to the UI
log panel **and** to `logs/medo.log` (`_push()`), remembers `last_request` so
`resume_after_login()` can replay an interrupted run, and auto-rebuilds `dist/` web bundle
+ PDF whenever the question count grew.

### 5.2 Download UX (recently reworked)
- **Minimizable download window**: `–` collapses it to a **floating corner bar** with live
  stats, ⏸ stop, «ادامه در پس‌زمینه» and «جزئیات». Closing the window while a run is
  active no longer hides it; on page reload a running job is re-read from the server and the
  bar reappears; the bar removes itself when the run finishes.
- **Multi-target (subtopic) download** logs real progress:
  `مبحث 4 از 13 — «سیروز کبدی» · تا اینجا 52 سوال` then `↳ «...»: 12 سوال ذخیره شد`.
  A subtopic already `completed` is **skipped** (`_target_progress`), so a previously
  downloaded 52-question topic is not fetched again.
- Internet-usage metering: per-question `questions.download_bytes`, per-run rows in
  `usage_log`, live «مصرف اینترنت: X (N سوال)» + estimate from completed runs only.
- Auto-resume after login (also for `limit`), `paused` (not `completed`) for empty pages,
  «ادامه دانلود نیمه‌تمام» button, `run_request` persisted in DB so a server restart can
  still resume.

### 5.3 Web UI (`medo/web/app.js` + `style.css`)
- Sidebar with two tabs: **«بانک من»** (only categories that have questions) and
  **«همه دسته‌ها»** (full site tree for downloading); lazy-built DOM (157 rows instead of 2,594,
  expansion 380 ms → 15 ms); per-node progress «۳۰/۹۵» + «ناقص» badges; download button on
  every row, category = download all (with confirmation), **«تفکیک»** button to queue all
  empty subtopics of a topic.
- Root order: دستیاری → پره انترنی → تالیفی → the rest (both desktop and phone bank).
- Question card: breadcrumb `category_path`, session bar (answered/accuracy + progress),
  vote-percentage bars, `<mark>` highlights, explanation accordion, lightbox for mindmaps,
  jump palette with filters (all/answered/bookmarked), keyboard shortcuts (1–5, ←/→, j/k, b, e, /, Esc).
- **Answers with no key are never shown as wrong**: no red state, excluded from accuracy,
  message «پاسخ در بانک نیست؛ غلط شمرده نمی‌شود», plus the site's vote share as a hint.
- «گوشی» modal: Wi-Fi transfer (all LAN IPs, copy + QR, 4 s refresh), USB
  (`adb reverse tcp:8765 tcp:8765`), APK download, **«ساخت APK تازه»** with build log, QR for
  `…/medo.apk` install/update, QR per address (library bundled at `medo/web/vendor/qrcode.js`,
  QR rendered at 184 px with a 4-module quiet zone).
- Responsive at 390/720/1440 px, contrast ≥ 4.5:1, `:focus-visible`, `aria-expanded`,
  skip link, `prefers-reduced-motion`, light-by-default theme (v3 visual refresh).
- State cards: loading skeleton, empty bank (with `medo share` command), server-unreachable
  (retry), no results (clear filters).

### 5.4 CLI (`medo/cli.py`)
```
medo login           # interactive browser login → data/storage_state.json
medo scrape          # --topic/--all, --refill-explanations, --max-minutes/--max-questions
medo discover        # hierarchy crawl (seeded from the entry landings)
medo serve           # --port (default 8080)
medo build-web       # alias export-web → dist/ static bundle
medo build-pdf       # alias export-pdf → dist/medo_test_book.pdf
medo build-bank      # alias export-android → dist/medo.medo (phone bank)
medo share           # read-only Wi-Fi server + APK page (port 8765)
medo stats | medo verify | medo import-fixtures
```
All path defaults come from `medo/paths.py` (never cwd-relative).

### 5.5 Desktop PDF (`medo/pdf.py` + `templates/book.html`)
- TOC page, sections ordered by **tree depth-first traversal** (not topic-id), questions
  numbered continuously *after* sorting so the key matches the printed numbers.
- **Answer key + explanation of a section are printed immediately after that section's
  questions** (with a page break), not as one block at the end of the book (this was an
  explicit user request).
- Images embedded as base64 data URIs; local `src` rewriting; `--partial` marks incomplete
  books «ناقص».

---

## 6. Android app — feature inventory

Plain Java, no Gradle/AndroidX, all UI built in code with `Ui.java` design tokens
(48 dp touch targets, Persian digits, reduced-motion respect).

- **MainActivity (home)**: title + «⇩ دریافت بانک», 3-column stats bar, search,
  «همهٔ سوالات» / «آزمون تصادفی», continue-card, tree list, help.
  Tapping a node with questions opens the question list; `⋯` button / long-press opens the
  menu (view questions, quiz, PDF, expand/collapse). An empty subtopic is never a dead end —
  it says which parent topic it is in and offers «نمایش N سوال والد» (`Bank.nearestWithQuestions`).
- **BrowseActivity**: list of a topic's/all questions with search over stem+options,
  filters (all/unanswered/starred/wrong), per-row correct/incorrect state, topic path,
  tap → **Quiz opens at that question** (`EXTRA_START_INDEX` + `Quiz.goTo`), site vote % per
  option, «شروع آزمون از این فهرست», PDF of the filtered list.
- **QuizActivity**: instant feedback, explanation, star, clear answer, result summary;
  unanswered-by-archive questions are never marked wrong and never counted in accuracy.
- **PdfExporter**: builds real A4 PDFs with `android.graphics.pdf.PdfDocument` +
  `StaticLayout` (RTL), header/page numbers, correct option in green, explanation, and an
  **answer-key page at the end of *each section*** (was: a single page at the very end —
  user rejected that). Output shared via `SharedFileProvider` (minimal ContentProvider).
  Built on a background thread with a ProgressDialog.
- **ScanActivity + QrDecoder + ScanTarget**: Camera1 preview (minSdk 21) + bundled zxing.
  Scanning a URL ending in `.apk`/`/medo.apk` → app install/update flow
  (`REQUEST_INSTALL_PACKAGES` + FileProvider + «نصب برنامه‌های ناشناس» guide); any other URL
  → bank download. Camera permission at runtime; denial/absence → manual path message.
- **Bank/BankStore/StateStore**: `.medo` JSON bank parsing, per-node counts computed from
  the index (not full scans), atomic writes to `filesDir/state.json` (answers, stars).
- **Internet permission exists but the app never talks to the internet** — only to the LAN
  transfer server (or a cable via `adb reverse tcp:8765 tcp:8765`).

Build/test:
```
bash android/build.sh   # → dist/medo.apk (~276 KB, v1.2, signed with android/medo-debug.keystore — KEEP this keystore)
bash android/test.sh    # JVM logic tests, currently "ALL PASSED (116 checks)"
```

---

## 7. Problems we hit → what worked → what did NOT work

> This is the section that saves the next agent the most time. Each item: symptom, root
> cause, fix, and the attempted fix that failed.

### 7.1 Parsing / data quality
| Problem | What did NOT work | What worked |
|---|---|---|
| Junk or incomplete stems stored («سوال واقعی نیست») | Falling back to a document-wide search for any visible paragraph — it always found site chrome (login page, stats widget, «پنل کاربری») | Reject widget/browser-check/login text (`_WIDGET_TEXT_MARKERS`, 12-char floor), require **both** `#label1` and a valid stem, wait the full patience (`DECRYPT_EXTRA_ATTEMPTS`) |
| Class-name selectors break every load | Any CSS-class selector | Id-based anchors + evaluating the obfuscation stylesheet's visibility rules |
| `#q-stem-container` "not found" | Believing the synthetic fixture's DOM | Real page uses `#qtxt` + random-class siblings; fixtures were misleading |
| Site chrome stored as a question | Treating any non-empty paragraph as a stem | `is_valid_stem_text()` + `classify_page()` with NFKC-normalized phrase lists (the site serves notices in Arabic presentation forms) |
| 67+ questions with `correct_index IS NULL` (20 %) | Guessing an answer from vote percentages | `medo/answer_audit.py`: re-open the exact page with the saved session, match the question by option signature, press «نمایش جواب», store only if the site reveals it — never guess. Aborts with a clear message after two consecutive load failures (network/proxy) |
| Splitting questions into subtopics locally | Any local heuristic — **the HTML carries no per-question tag** | Re-read the site's `?tag=T` filtered pages: `scripts/backfill_tags.py`, triggered from the UI via `POST /api/tags/sync` **with the saved session** |

### 7.2 Download / resume
| Problem | What did NOT work | What worked |
|---|---|---|
| Run ends `completed, 0 saved` while the page is still decrypting | 1–2 retry attempts | Extra ~24 s of patience while `«در حال پردازش»` is on screen; widget-only pages classify as `auth`, not `empty` |
| Login redirect reported as success | Checking only one tab's URL for `/dashboard` | `evaluate_login_signal()`: dashboard document 200 **or** tab URL + same-tab `fetch` confirmation **or** rotated `sessionid` + left `/login` |
| False-positive login (guest session saved over a good one) | «پنل کاربری» text + `sessionid` presence — **fires for every guest** (Django sets a session for anonymous users) | Only the site's own `/dashboard/welcome` proof; the session file is written **only** after a signal, so a cancelled login can't clobber a good session |
| "site unreachable (SSLCertVerificationError)" for a valid login | Raw Python socket/TLS checks — this host serves an extra cross-signed cert that OpenSSL rejects while Chrome builds the path fine | Run every session/login check **inside Chromium**, with a 90 s cache and a lock so the 5 s UI poll can't launch two browsers |
| Resume lands on the wrong page (`topic shorter than resume target`) | Trusting `data-page` of the first «بعدی» button — the widget renders duplicates and the first is stale | Position comes from the page's own counter «سوال N از M» (Persian digits supported); `_click_next` tries buttons from last to first and verifies a fingerprint change |
| Whole run crashed: `sleep length must be non-negative` | Ignoring the race | Compute `remaining` once, sleep only if positive; `run_questions` now uses `max(total_saved, scraped_count)` so a crashed run still counts |
| Byte counter froze at one value | Counting only `Content-Length` — the site serves next pages through `fetch` + Cache API, so those responses have no length header (and negative `responseBodySize`) | `Request.sizes()` and a `resp.body()` fallback; averages come from **completed** `usage_log` rows only (the live ratio shrank on every question because of prefetch) |
| Subtopics downloaded but questions all landed in the parent topic | — | `questions.tag_id` + `subtopic_title`, a `micro_state(topic_id, tag_id)` cursor table, `topic_url(..., tag_id=)`, `reconcile_categories()` |

### 7.3 Network / environment
| Problem | What did NOT work | What worked |
|---|---|---|
| 403 «مرورگر پشتیبانی نمی‌شود» in headless mode | TLS tricks, headed-mode user agent | Real Chrome UA + `AutomationControlled` disabled + full fingerprint init script (proved by an A/B/C probe: only UA+stealth turned 403→200) |
| Site unreachable: `medofast.ir → 198.18.0.94` | Waiting / retrying | DoH-resolve the real IP and pin with `--host-resolver-rules` |
| …then the **pin itself broke everything** (`SSL connect error` on a tunneled network) | Always pinning whenever DNS looks fake | `netfix.reachable()` now handshakes **both** paths and pins only when the system path fails and the pinned one succeeds — on the current network no pin is used |
| «اینترنت قطع است» on the login page while other sites work | Debugging the app | Windows `ProxyEnable=1` with nothing on `127.0.0.1:10808`; `netfix.proxy_problem()` diagnoses it, `--no-proxy-server` is tried, and the user is told to start the VPN or disable the proxy |
| Guest quota misreported as «نیاز به ورود» | Treating any `/login` redirect as `auth_required` | `is_guest_quota()` → status `limit` + resume-after-login enabled for it |
| 429 during subtopic backfill finished with **zero** mappings | 0.12 s delays, continuing to hammer the next tag after being throttled | `--delay 1.2`, honor `Retry-After`, exponential backoff, **abort the whole run** when the site keeps limiting, skip already-tagged (`tagged_count`) on the next run |
| No way to tell *why* a download failed | In-memory-only controller log | `medo/applog.py` → `logs/medo.log` (rotating, line-buffered tee of stdout/stderr) + a `[diag] … http=… url=… body=…` line for every failed page; new status `throttled` («۱۵ تا ۳۰ دقیقه صبر کن») instead of a generic failure |

### 7.4 Web/desktop UI
| Problem | What did NOT work | What worked |
|---|---|---|
| Patched UI never showed up | Service worker caching `/api/*` cache-first + HTTP caching of `app.js` | SW caches fonts/icons only, `/api/*` is network-only, server sends `no-store` and stamps `?b=<mtime>` |
| Modal button «شروع دانلود سوالات» did nothing | — | Two functions were both named `startScrape`; the second (form) hoisted over the first and sent `targets=undefined` → renamed to `startScrapeFromForm` |
| XSS through the sanitizer | Attribute values re-serialized unescaped after `HTMLParser` decoded them (`<i class='x" onmouseover="alert(1)'>`) | `html.escape(..., quote=True)` + attribute names restricted to `[a-zA-Z][a-zA-Z0-9_-]*` |
| Search always empty | FTS5 was defined on a non-existent `options_text` column and `CREATE VIRTUAL TABLE IF NOT EXISTS` swallowed the error | Detect the bad definition, rebuild, compare `questions_fts_docsize` row count |
| Server depended on the caller's cwd | `Path("data/medo.db")` defaults | `medo/paths.py` single source; explicit args still win |
| Local server leaks | — | Host allowlist (DNS rebinding), `resolve()`+`relative_to` (path traversal), exceptions → Persian JSON, unread body drained before 403 |
| Answers/bookmarks not really persisted | Fire-and-forget POSTs, `/api/bookmark` never called, `medo stats` always 0 | Validated `/api/answer` (range-checked, `0/null` clears), idempotent explicit-state `/api/bookmark`, merge localStorage↔archive on load, no requests on `file://` |
| Download window hid the running job | Simply minimizing it (job became invisible) | Floating corner bar with stop/background/details + restore-on-reload |

### 7.5 Android
| Problem | What did NOT work | What worked |
|---|---|---|
| QR scan did **nothing** while the camera clearly saw the code | Bigger QR / quiet zone alone (that was only half of it) | `Camera.setPreviewCallback` hands you **the same array every frame**; the async decoder was reading it while the camera overwrote it → **`data.clone()` per frame** |
| …still unreadable on some ROMs | Assuming `width` == row length | `QrDecoder.strideOf()` handles **padded buffers** (stride > width, caused by preview rounding); decoder tries 4 rotations, `ALSO_INVERTED` (dark backgrounds), falls back to `GlobalHistogramBinarizer`; decode logic is pure + JVM-tested; scanner hint changes after ~20 s; desktop QR made 184 px with a 4-module quiet zone |
| PDF export did nothing | `PrintManager.print()` — ROMs without a print spooler silently drop it | Direct `android.graphics.pdf.PdfDocument` writing to the app's Documents, then share/open dialog |
| Answer key was a single page at the end | That layout (user rejected it) | `PdfExporter.sectionOf` + `answerKey(group, first, label)`: key + explanation right after each section, continuous numbering; inline green correct-option colour kept |
| Giant empty box on the home screen | — | `stats` was built as a **vertical** `Ui.card` while children used `weight(1f)` (weight = height in a vertical parent) → the stats bar ate the screen; now it is a `Ui.row` |
| Empty subtopic = dead end | Showing 0 with no explanation | Parent-aware dialog + `Bank.count` no longer double-counts parent questions (shows `0 از ۳۹` separately from `39`) |

### 7.7 Phases 0-6: Pro lesson, identity, subtopics, repair, search, phone bank

| Problem | What did NOT work (the old approach) | What worked |
|---|---|---|
| A Pro lesson contained three decoy paragraphs from unrelated topics (cellulitis after freshwater swimming, self-harm, serology), hidden only by a rule in a dynamic stylesheet (`cid:css-…`) that is not in the HTML | `dom.inner_html(#block-b)` and keeping all 12 `.element-container` blocks; filtering by `data-order`; trusting the HTML text | In the live page, mark every node under `#qtxt`/`#block-b` whose computed style is hidden/off-screen (`data-medo-hidden`) and let the parser skip it; also skip blocks whose `data-element-id` is a hyphenated UUID; offline, inline `cid:` CSS and strip `@media print` blocks before evaluating visibility |
| A free page with a full 710-character explanation was stored as LOCKED | Searching the lesson text for `«پرو»`, `Pro`, `«اشتراک»` **anywhere** — «پروگنوز», «پروتئین», «Procalcitonin» trigger it | Decide LOCKED from the site's lock overlay, or from a **short** (< 200 chars) text with whole-word phrases |
| Stored lessons carried U+FE00..U+FE0F variation selectors and a trailing «MedoFast.ir» watermark | The previous invisible set in three separate regexes | One shared set in `medo/text_utils.py` (adds FE00-FE0F, soft hyphen, bidi controls, BOM) imported everywhere; watermark as a noise rule |
| Persian half-spaces (ZWNJ) disappeared from stored text | Stripping ZWNJ in every cleaning pass | Keep ZWNJ in display fields; strip it only in the content hash, option signatures and search normalization |
| A subtopic download at page 1 followed the session's remembered position, stored one question and marked the topic complete | Requesting `?tag=T` without `page=` and trusting what the site opened | Always request the page explicitly, read the site's counter and walk back to the target; if it cannot be positioned, stop `paused` with a diagnostic — never `completed` |
| A 429 during tag mapping lost a whole topic's work | Committing only after **all** subtopics of a topic | Commit per page into `question_nodes` + `tag_sync_state(topic_id, tag_id, last_page, status)`; a re-run resumes at the exact page |
| A question shared by several subtopics kept only the last one (and flipped on re-download) | One `topic_id`/`tag_id` pair per question with `coalesce(new, old)` | `question_nodes` (many-to-many) + `site_question_id` with a unique index; every count/filter reads nodes |
| The UI downloaded and shipped the whole bank as one `SELECT *` JSON | Returning every row with its HTML and rebuilding the whole PDF after each run | Light paged `/api/questions` + `/api/question/<id>` on demand; PDF only when «ساخت PDF» is pressed |
| Search missed «كتاب» (Arabic kaf), «سؤال ۱۲۳» (Persian digits) and words with invisible selectors | `text.toLowerCase().includes(query)` over raw fields, on every keystroke | One canonical form shared by Python/JS/Java (`tests/search_vectors.json`), folded once per question at load, 200 ms debounce |
| FTS5 re-indexed a question on every answer/bookmark click | A trigger on `AFTER UPDATE` of the whole row | `AFTER UPDATE OF stem_text, explanation_text, options_json` |
| A clean clone could not build the APK, and every build shipped `versionCode 1` (Android refuses to install it over a newer one) | Adding the manifest to git by hand while the blanket `*.xml` ignore stayed; hardcoded `--version-code 1` | Remove the blanket rule (keep `/coverage.xml`), track `android/AndroidManifest.xml` + `android/res/**`, read versionCode/Name from the manifest and bump the code only after a signed APK exists |
| The phone held the bank three times over (`byte[]` → `String` → parsed tree) and could run out of memory | Only making the JSON smaller/compressed | `.medo` v2 = zip: `index.json` (tree, counts, light entries) + `chunks/<category>.json`; the app reads the index first and streams one chunk at a time; v1 JSON still loads |

### 7.6 Research answers that closed a question
- **«نسخه پرو» (MedoFast Pro)**: researched — it is a *subscription with detailed answer
  keys*; there is **no evidence of a separate site or hidden client code**. So our recovery
  tooling works through the user's own logged-in session and reports honestly when the site
  does not reveal an answer.
- **Category order** requested by the user: دستیاری → پره انترنی → تالیفی → the original
  order, implemented once in `db.root_rank()` and consumed by both the web UI and `dist/medo.medo`.

---

## 8. Current state (as of the end of phases 0-6, 2026-10-28)

**Verified**
- `./.venv/Scripts/python -m pytest -q` → **283 passed** (plus 11 skips on a clean
  clone only when `data/diag` has no real capture).
- `bash android/test.sh` → **ALL PASSED (140 checks)** (zip bundle, word search, shared
  normalization vectors, padded-stride QR, section answer keys).
- `bash android/build.sh` → `dist/medo.apk` **v1.2**; versionCode now comes from
  `android/AndroidManifest.xml` and is bumped to 3 after a successful build.
- `node --check medo/web/app.js`, `node --check medo/web/normalize.js`, `python -m compileall medo`
  clean.
- Git: code only on `master` (phase commits; latest is the phone-bank zip). `launcher.log`
  untracked, blanket `*.xml` ignore removed, Android manifest + `res/` tracked.

**What phases 4-6 changed (in one breath)**
- `questions.fetched_as` (`guest`/`login`/`unknown`) + `NEEDS_REPAIR_SQL`: a repair queue
  that means something (no key, no/partial/locked lesson, or a guest page).
- `medo/answer_audit.py` is now one resumable «ترمیم پاسخ‌ها و درس‌نامه‌ها» job: site id or
  option signature identity, per-question commit, mind-map images via
  `Harvester._store_assets`, stops on `limit`, requires a saved session.
- `medo/doctor.py` + `medo doctor` + `/api/doctor`: proxy/DNS/TLS/session/site-id, one
  Persian verdict, blocks scrape/repair when the path is red.
- `medo/pacing.py` (`AdaptiveDelay`): starts 1.5-3 s, doubles on 429 up to 12 s, honours
  `Retry-After`, shrinks 10% after 40 clean pages, floor 1.2 s; wired into `Harvester`.
- One `threading.Lock` around the job slot (`_take_slot`); PDF builds on request only
  (`/api/pdf`, «ساخت PDF کتاب», `medo build-pdf`), and it does not need the network.
- Shared search normalization (`medo/text_utils.normalize_search_text`,
  `medo/web/normalize.js`, `android/.../Text.java`, contract in `tests/search_vectors.json`).
- `/api/questions` pages light rows (`offset`/`limit`, no HTML) and
  `/api/question/<id>` serves the full body; the UI fetches a body only when it opens a
  lesson and folds each question's search text once at load.
- FTS trigger narrowed to `AFTER UPDATE OF stem_text, explanation_text, options_json`.
- `.medo` is now a zip (index + one chunk per top-level category) read chunk by chunk;
  the old single-JSON file still loads. The phone also has a word index for search, and
  `build-bank` can ship selected categories or embed WebP mind-maps.

**Known-open (do not claim these are done)**
1. **Live verification against medofast.ir is still blocked on this machine** — no real
   login, no live scrape, no live repair, no live tag sync in these sessions. Everything
   is unit-tested and logs its evidence (`medo doctor`, `medo diagnose-live`, `logs/medo.log`).
2. **The `.medo` zip loads its chunks eagerly (streamed one at a time), not lazily with an
   LRU of 2.** The memory problem that mattered was the three copies of the whole file
   (`byte[]` → `String` → tree); this removes them while staying with an immutable
   `Question`. A lazy per-chunk cache is the next step if a bank grows past the phone's RAM.
3. **QR scanner and phone PDF have never been run on a real device** — only JVM tests and
   compilation.
4. `Image` transfer to the phone is opt-in (`build-bank --images`); the default bank is
   text-only, which is why it stays small.
5. FTS5 is now used only by tests; the browser searches its own normalized index. Either
   wire it to a server-side search or delete the table (see PROMPT §11).
6. `data/storage_state.json` may be anonymous — one real «ورود با مرورگر» unlocks
   explanations and the daily quota.

---

## 9. Commands you will need

```bash
# Python env
./.venv/Scripts/python -m pytest -q          # full suite (expect 285 passed)
./.venv/Scripts/python -m compileall medo    # syntax
node --check medo/web/app.js                 # JS syntax
node --check medo/web/normalize.js

# Desktop app
./.venv/Scripts/python launcher.py           # server + browser (or the desktop shortcut)
./.venv/Scripts/python -m medo.cli doctor     # Persian verdict before any download
./.venv/Scripts/python -m medo.cli serve --port 8099
./.venv/Scripts/python -m medo.cli discover  # hierarchy crawl (needs internet)
./.venv/Scripts/python -m medo.cli build-web | build-pdf | build-bank | share
./.venv/Scripts/python -m medo.cli build-bank -c 12 -c 15 --images   # phone bank
./.venv/Scripts/python -m medo.cli keystore-backup                   # zip + SHA-256

# Logs (read these FIRST when something fails)
tail -f logs/medo.log        # durable app log (server, scraper, discover, login, backfill)
tail -f launcher.log         # launcher output

# Android
bash android/test.sh         # JVM logic tests (no emulator)
bash android/build.sh        # → dist/medo.apk (needs tools/jdk8 + tools/sdk, already present)

# Phone transfer
# UI: header «گوشی» button → start transfer → scan QR, or:
adb reverse tcp:8765 tcp:8765   # for USB
```

---

## 10. Suggested first steps for a new agent

1. Read `AGENTS.md` (coding rules), then `NOTES.md` tail for the last session's evidence.
2. Run `pytest -q` and `android/test.sh` to establish the baseline before touching anything.
3. When a feature "does not work", **read `logs/medo.log` first** — nearly every bug in §7
   was identified from a log line, not from guessing (429 statuses, `[diag] http=…`,
   `[login] …`, `ImportError: PlaywrightError`, `saved=0 detail=could not position at page 1`).
4. Never add a dependency, framework or abstraction the repo does not already use; the
   project deliberately has none for parsing, HTTP, PDF (desktop uses Chromium) or Android UI.
5. If you must test against the live site, start small (`--max-pages 2`), keep the 1.2 s
   delay, and expect to be throttled after ~50 requests.

---

## 11. Phase map (where each fix lives) and what is left

This is the file-level map of the work described in §7.7. Read it before you
change one of these areas; each line was verified by the check named at the end.

| Area | Files | Check |
| --- | --- | --- |
| Pro lesson: decoys, invisibles, lock detection | `medo/parser.py`, `medo/html_utils.py`, `medo/text_utils.py`, `medo/scraper.py` | `tests/test_parser.py`, `scripts/dev/check_captures.py` (needs `data/diag/`) |
| Reveal waiting (poll, not a fixed sleep) | `medo/scraper.py` (`_wait_for_block_b`), `medo/answer_audit.py` | `tests/test_scraper_helpers.py` |
| Stable site id | `medo/parser.py`, `medo/db.py`, `medo check-site-ids` | `tests/test_db.py`, `tests/test_parser.py` |
| Many-to-many nodes | `medo/db.py` (`question_nodes`), `medo/share.py`, `medo/web/app.js`, `android/.../Bank.java` | `tests/test_db.py`, `tests/test_android_bundle.py`, `android/test.sh` |
| Subtopic positioning + strategy + backfill | `medo/scraper.py` (`_position_at`, `subtopic_targets`), `scripts/backfill_tags.py`, `medo/server.py` | `tests/test_scraper_helpers.py`, `tests/test_backfill_tags.py` |
| Repair queue + login tier | `medo/answer_audit.py`, `medo/db.py` (`fetched_as`, `NEEDS_REPAIR_SQL`), `medo/server.py` | `tests/test_answer_audit.py`, `tests/test_server_api.py` |
| Readiness + pacing + job lock | `medo/doctor.py`, `medo/pacing.py`, `medo/scraper.py`, `medo/server.py` | `tests/test_doctor.py`, `tests/test_pacing.py`, `tests/test_server_api.py` |
| Search normalisation | `medo/text_utils.py`, `medo/web/normalize.js`, `android/.../Text.java`, `tests/search_vectors.json` | `tests/test_search_normalization.py`, `android/test.sh` |
| Light API + paging | `medo/db.py` (`get_questions_light`), `medo/server.py`, `medo/web/app.js` | `tests/test_server_api.py` |
| Phone bank v2 | `medo/share.py`, `medo/cli.py`, `android/.../Bank.java`, `android/.../BankStore.java` | `tests/test_android_bundle.py`, `android/test.sh` |
| Build hygiene | `.gitignore`, `android/build.sh`, `android/AndroidManifest.xml`, `medo/cli.py` (`keystore-backup`) | `tests/test_android_bundle.py`, `tests/test_cli_app.py` |

**Known-open work, in the order it should be done**

1. **Live proof.** The machine still cannot reach the site reliably. With the VPN
   routed direct (`domain:medofast.ir`, `geosite:ir`), run `medo doctor --deep`,
   then one small scrape, then `diagnose-tags` and one repair run. Record the
   output in `NOTES.md`; until then do not claim those paths work live.
2. **Lazy phone chunks.** The zip already loads one chunk at a time, but all
   questions end up in memory after load. If a real bank gets too large, make
   `Bank` hydrate a chunk on `question(id)`/`forNode(node)` and keep an LRU of 2,
   replacing the immutable `Question` entries in place (the map already exists:
   `positions`).
3. **FTS5 decision.** It is now only exercised by tests. Either wire
   `/api/questions?q=` to FTS5 with a quoted-token query builder, or drop
   `questions_fts` and its triggers and delete `search_questions` + its tests.
4. **Device test.** Install the fresh `dist/medo.apk` and a zip bank on the
   phone: QR import, browse, quiz, PDF export, word search.
5. **Images to phone.** `build-bank --images` inlines WebP data URIs. Verify on a
   device that a large lesson with a mind-map still opens quickly, and consider a
   per-category image switch in the UI.
