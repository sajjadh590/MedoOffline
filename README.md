# MedoOffline — آرشیو آفلاین بانک سوالات مدوفست

A local, offline-first mirror of the Persian medical Q&A site **medofast.ir**.
It archives multiple-choice questions (stem, options, vote percentages, correct
answer and the Pro lesson), then lets you study them **with no internet at all**:

* **Desktop** — a Python CLI plus a local web app at `127.0.0.1` (Persian RTL,
  light/dark theme, keyboard shortcuts).
* **Android** — a dependency-free Java app (`dist/medo.apk`, ~280 KB) that reads
  a transferred `.medo` bank and works fully offline.

The archive lives in `data/medo.db` on this PC. GitHub holds **code only** — no
scraped text, no session, no builds.

---

## راه‌اندازی سریع

```bash
python -m venv .venv
.venv/Scripts/pip install -e .[dev]      # Windows (bash: .venv/bin/pip)
playwright install chromium              # only for the bundled browser fallback

python -m medo.cli login                 # opens a browser; log in yourself
python -m medo.cli doctor                # «آماده استخراج» or what is wrong
python -m medo.cli scrape --topic 79     # or use the web UI
python -m medo.cli serve                 # http://127.0.0.1:8080
```

`login` stores the session in `data/storage_state.json` (git-ignored). Nothing
here touches, stores or asks for your password; the site's own login page does.

---

## دستورها

Every flag below comes from the command's own `--help`.

| Command | What it does | Main flags |
| --- | --- | --- |
| `login` | Open the site and save the signed-in session | `--state` |
| `doctor` | One Persian verdict: can a download work now? | `--deep`, `--topic`, `--db` |
| `discover` | Crawl the site's menu once and store its exact tree | `--headless`, `--state`, `--db` |
| `scrape` | Download one topic, one subtopic (`--tag`) or everything | `--topic`, `--all`, `--start`, `--max`, `--max-minutes`, `--max-questions`, `--refill-explanations`, `--whole-topic`, `--delay-min`, `--delay-max`, `--headless`, `--state`, `--db` |
| `serve` | Local web app | `--port`, `--db` |
| `build-pdf` | Printable book, built only when asked | `--output`, `--topic`, `--title`, `--answers`, `--partial`, `--db` |
| `build-web` | Self-contained static web bundle (works over `file://`) | `--output`, `--topic`, `--partial`, `--db` |
| `build-bank` | `.medo` bank for the phone | `--output`, `--category`, `--images`, `--db` |
| `share` | Read-only Wi-Fi server for the phone | `--port`, `--bind`, `--db` |
| `stats` / `verify` | Counts and integrity checks | `--db` |
| `diagnose-tags` | Stored questions without a subtopic, truncated subtopics | `--db` |
| `diagnose-live` | One live probe: page counter, `?tag=` filtering | `--topic`, `--tag`, `--headless`, `--state` |
| `check-site-ids` | Fail loudly if the site stops exposing its question id | `--topic`, `--tag`, `--headless`, `--state` |
| `import-fixtures` | Import synthetic/captured MHTML into a test DB | `--db` |
| `keystore-backup` | Zip the Android signing key + its SHA-256 into `dist/` | `--keystore`, `--output` |

`export-pdf`, `export-web` and `export-android` are kept as aliases of
`build-pdf`, `build-web` and `build-bank`.

### نرخ درخواست‌ها

One request at a time. The pause starts at **1.5–3 s**, doubles on a `429`
(up to 12 s, honouring `Retry-After`) and settles back after 40 clean pages,
never below 1.2 s. A run that hits a quota or a login wall stops with a
resumable cursor instead of pretending the topic finished.

---

## کارهایی که رابط وب انجام می‌دهد

* **دانلود**: click a topic/category in the sidebar. When the topic has
  subtopics, the download queues the subtopics (so every question lands in the
  right `?tag=` slice), then one reconciliation pass for anything missing.
* **ترمیم پاسخ‌ها و درس‌نامه‌ها**: re-reads only the questions the signed-in
  session could still improve — no answer, no/partial/locked lesson, or a page
  stored as a guest. It commits per question and stops cleanly on a daily limit.
* **پخش سوال‌ها در زیرمبحث‌ها**: maps already-stored questions to their
  subtopics, resuming page by page after a `429`.
* **بررسی شبکه و آمادگی دانلود** (`/api/doctor`): proxy / DNS / TLS / session /
  site-id check with a Persian verdict.
* **ساخت PDF کتاب**: built on demand for the selected category (or the whole
  bank) instead of after every download.
* **جستجو**: one canonical form for Persian (Arabic ي/ك → ی/ک, ZWNJ and the
  site's invisible variation selectors removed, Persian/Arabic digits → ASCII,
  case folded) shared by Python, the browser and Android.
* Shortcuts: `1`–`5` pick an option, `j`/`k` or arrows navigate, `b` bookmark,
  `e` explanation, `/` search, `Esc` closes dialogs. The theme has a light
  default and a dark mode.

---

## آندروید

### ساختن APK

The build is offline and needs the portable toolchain in `tools/` (JDK 8 +
Android SDK build-tools 30.0.3). It is not in git:

```bash
bash android/build.sh          # -> dist/medo.apk
bash android/test.sh           # pure-Java logic tests on the desktop JVM
```

`versionCode`/`versionName` are read from `android/AndroidManifest.xml` and the
code is bumped after a successful build, so an update installs over the old app.
`android/medo-debug.keystore` must exist — the same key is required to update an
installed app; back it up with `medo keystore-backup` (it never enters git).

### رساندن بانک به گوشی

```bash
python -m medo.cli build-bank                    # whole bank, no images
python -m medo.cli build-bank -c 12 -c 15        # only those categories
python -m medo.cli build-bank --images           # embed small WebP mind-maps
python -m medo.cli share                         # or over Wi-Fi, read-only
```

The `.medo` file is a **zip**: `index.json` (tree, counts, one light entry per
question) and `chunks/<category>.json` with the lesson bodies. The app reads the
index first and streams each chunk straight out of the archive, so a large Pro
bank never sits in memory three times over. Banks built by older versions (one
JSON document) still open.

In the app: **دریافت بانک → از فایل** (or **دریافت از وای‌فای**). Over USB,
`adb reverse tcp:8765 tcp:8765` makes the Wi-Fi address reachable at
`127.0.0.1`.

---

## وقتی چیزی کار نمی‌کند

Run `medo doctor` first. It answers with one of three verdicts:

* **«آماده استخراج»** — the path and session are fine.
* **«نشست منقضی شده؛ دوباره وارد شو»** — run `login` again; downloads pause
  instead of continuing as a guest.
* **«اختلال در مسیر شبکه یا فیلترشکن»** — a dead Windows proxy, a fake-IP DNS
  answer that the tunnel does not carry, or TLS being reset.

You do **not** have to switch the VPN off for every download: in the VPN client
(v2rayN, Nekoray, Clash, Hiddify) add `domain:medofast.ir` and `geosite:ir` to
the DIRECT/bypass rules. The scraper browser already runs with
`--no-proxy-server`.

A `429` is the site's rate limit, not your internet: stop, wait 15–30 minutes,
then resume — the cursor is kept.

If the Android build says the signing key is missing, restore
`android/medo-debug.keystore`; creating a new key installs a second app instead
of updating the first.

---

## داده‌ها و حریم خصوصی

* `data/` — SQLite archive, session, diagnostics. **Never committed.**
* Scraped pages in `data/diag/` contain paid lesson text. Never commit them,
  never paste their text into an issue or a commit message.
* `dist/`, `build/` — generated bundles, PDF, APK. Not committed.
* `tools/`, `.venv/` — local toolchains (~600 MB). Not committed.
* The app never bypasses a login, a quota or a paywall: explanations come from
  the account you logged in with, at the site's own pace.

---

## ساختار پروژه

```
medo/                Python package (CLI, server, scraper, parser, DB, export)
medo/web/            Local web UI (no CDN: fonts, icons and JS are bundled)
medo/templates/      Jinja template for the printed book
android/src/         Dependency-free Java app
android/tests/       JVM logic tests (TestMain)
scripts/             One-off diagnostics and dev helpers
tests/               pytest suite + shared fixtures
```

Read [`AGENTS.md`](AGENTS.md) before changing code (smallest complete change,
stdlib first, Persian UI), [`PROMPT.md`](PROMPT.md) for the full project brief
and every bug fixed so far, and [`NOTES.md`](NOTES.md) for the chronological
evidence.

## آزمون‌ها

```bash
pytest -q                # Python: parser, DB, server, export, bundle, PDF HTML
node --check medo/web/app.js
node --check medo/web/normalize.js
bash android/test.sh     # Java logic on the desktop JVM
```

`tests/conftest.py` generates the synthetic MHTML fixtures when they are absent,
so a clean clone needs no extra step. Real captures are never generated; tests
that need one skip themselves.
