"""
medo/scraper.py
Polite, resumable, UI-driven Playwright harvester for medofast.ir.

Design (validated against the real page capture, see NOTES.md):
  * One request at a time, uniform random delay 1.5-3 s, exponential backoff
    on 429/503, immediate clean stop on 403/captcha/quota. Never parallel.
  * Pagination is driven through the real UI (button[data-nav-role="next"])
    — the underlying mechanism (XHR vs document) is irrelevant then. A
    `?page=N` fast path is attempted for resume and *verified* via the
    next-button's data-page before being trusted; otherwise we walk forward
    from page 1 by clicking.
  * Question detection uses the real parser (visibility-aware stem), never a
    hard-coded stem id (the real site has none).
  * Every parsed question is committed to SQLite (WAL) in its own transaction
    the moment it is parsed; a per-topic cursor (scrape_state) makes restarts
    land on the exact page.
  * SIGINT/SIGTERM: finish the current question, save state, print summary.
  * --max-minutes / --max-questions budgets stop the run cleanly.
  * Browser traffic is restricted to documents + XHR/fetch + JS/CSS; images,
    fonts, media, ads and trackers are aborted (needed images are downloaded
    once, deduplicated, through medo.assets).
"""
from __future__ import annotations

import hashlib
import random
import re
import signal
import time
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from medo.assets import download_asset
from medo.auth import DEFAULT_STORAGE_STATE, has_storage_state
from medo.db import (
    DEFAULT_DB_PATH,
    get_asset_by_url,
    get_connection,
    get_scrape_state,
    init_db,
    topics_needing_refill,
    update_scrape_state,
    upsert_asset,
    upsert_question,
)
from medo.models import ExplanationStatus, ParsedQuestion
from medo.parser import parse_question_html

BASE_URL = "https://medofast.ir"
TOPIC_URL = BASE_URL + "/qbank/topic-id-{topic_id}"
MENU_URL = BASE_URL + "/main-menu"

# --- politeness -------------------------------------------------------------
DELAY_MIN, DELAY_MAX = 1.5, 3.0
BACKOFF_BASE, BACKOFF_MAX = 5.0, 120.0
MAX_CONSECUTIVE_ERRORS = 5

# --- request filtering ------------------------------------------------------
BLOCKED_URL_PATTERNS = [
    re.compile(r"yektanet", re.I),
    re.compile(r"mediaad", re.I),
    re.compile(r"google-analytics", re.I),
    re.compile(r"googletagmanager", re.I),
    re.compile(r"clarity\.ms", re.I),
    re.compile(r"hotjar", re.I),
    re.compile(r"facebook\.net|fbcdn", re.I),
    re.compile(r"\.(woff2?|ttf|otf|eot)(\?.*)?$", re.I),
]
# Everything else that is not core to rendering text content is aborted;
# needed images are fetched separately (once, deduplicated) via medo.assets.
ALLOWED_RESOURCE_TYPES = {"document", "xhr", "fetch", "script", "stylesheet"}

# --- page classification phrases -------------------------------------------
LIMIT_PHRASES = (
    "خرید اشتراک",
    "پلن پرو",
    "اشتراک ویژه",
    "نسخه پرو",
    "ارتقا حساب",
    "محدودیت روزانه",
    "سقف روزانه",
    "ظرفیت پاسخ",
)
LOGIN_PHRASES = ("ورود به حساب کاربری", "وارد شوید", "ورود / ثبت نام")


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without a browser)
# ---------------------------------------------------------------------------

def topic_url(topic_id: int, page: Optional[int] = None) -> str:
    url = TOPIC_URL.format(topic_id=topic_id)
    return f"{url}?page={page}" if page else url


def read_current_page(html: str) -> Optional[int]:
    """Current page number derived from the next button's data-page (= N+1).

    Returns None when the marker is not readable (JS may set it late).
    """
    m = re.search(r'<button[^>]*data-nav-role="next"[^>]*data-page="(\d+)"', html)
    if not m:
        m = re.search(r'<button[^>]*data-page="(\d+)"[^>]*data-nav-role="next"', html)
    return int(m.group(1)) - 1 if m else None


def next_button_state(class_attr: str = "", style_attr: str = "", present: bool = True) -> str:
    """'ready' | 'disabled' | 'missing' — drives topic-completion detection."""
    if not present:
        return "missing"
    cls = (class_attr or "").lower()
    style = re.sub(r"\s+", " ", (style_attr or "").lower())
    if "turned-off" in cls:
        return "disabled"
    if "pointer-events: none" in style:
        return "disabled"
    if re.search(r"(?:^|;\s*)opacity:\s*0(?:\.0+)?(?:\s*;|$)", style):
        return "disabled"
    return "ready"


def classify_page(body_text: str, has_question: bool) -> str:
    """'question' | 'limit' | 'auth' | 'empty' for a rendered page."""
    if has_question:
        return "question"
    if any(p in body_text for p in LIMIT_PHRASES):
        return "limit"
    if any(p in body_text for p in LOGIN_PHRASES):
        return "auth"
    return "empty"


def fingerprint(html: str) -> str:
    """Cheap identity of the currently rendered question (options + length)."""
    opts = re.findall(r'data-encoded-text="([^"]+)"', html)
    key = "|".join(opts) + "#" + str(len(html))
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()


def needs_submit(parsed: ParsedQuestion) -> bool:
    """True when the answer key is not derivable and a submit click is needed."""
    return parsed.correct_index is None and bool(parsed.options)


# ---------------------------------------------------------------------------
# Harvester
# ---------------------------------------------------------------------------

class ScraperStopped(Exception):
    """Clean, intentional stop (auth/limit/blocked/captcha/interrupt/budget)."""

    def __init__(self, status: str, message: str = ""):
        super().__init__(message or status)
        self.status = status
        self.message = message


class Harvester:
    """Polite question scraper. Sequential, resumable, interrupt-safe."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        storage_state: Optional[Path | str] = None,
        delay_range: tuple[float, float] = (DELAY_MIN, DELAY_MAX),
        headless: bool = True,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        max_minutes: Optional[float] = None,
        max_questions: Optional[int] = None,
        assets_dir: Path | str = "data/assets",
    ) -> None:
        import os
        self.db_path = Path(db_path)
        if storage_state is None:
            storage_state = os.environ.get("MEDO_STORAGE_STATE", DEFAULT_STORAGE_STATE)
        self.storage_state = Path(storage_state)
        self.delay_min, self.delay_max = delay_range
        self.headless = headless
        self.on_progress = on_progress
        self.max_minutes = max_minutes
        self.max_questions = max_questions
        self.assets_dir = Path(assets_dir)

        self._interrupted = False
        self._deadline: Optional[float] = None
        self.run_started = time.monotonic()
        self.bytes_total = 0
        self._bytes_this_question = 0
        self._question_times: list[float] = []
        self._question_bytes: list[int] = []
        self._estimate_printed = False

    # -- signals / budgets ---------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        def _handler(sig, _frame):
            print("\n[!] Graceful stop requested — finishing the current question...")
            self._interrupted = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass  # non-main thread (e.g. tests)

    @property
    def elapsed_minutes(self) -> float:
        return (time.monotonic() - self.run_started) / 60.0

    def _stop_reason(self, scraped_count: int) -> Optional[str]:
        if self._interrupted:
            return "interrupted"
        if self.max_questions is not None and scraped_count >= self.max_questions:
            return "max_questions"
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return "max_minutes"
        return None

    def _polite_delay(self) -> None:
        """Uniform random delay; interruptible in 250 ms slices."""
        delay = random.uniform(self.delay_min, self.delay_max)
        end = time.monotonic() + delay
        while time.monotonic() < end and not self._interrupted:
            time.sleep(min(0.25, end - time.monotonic()))

    # -- browser -------------------------------------------------------------

    def _route_interceptor(self, route) -> None:
        req = route.request
        if any(p.search(req.url) for p in BLOCKED_URL_PATTERNS):
            route.abort()
            return
        if req.resource_type in ALLOWED_RESOURCE_TYPES:
            route.continue_()
        else:
            route.abort()

    def _on_response(self, resp) -> None:
        try:
            hdr = (resp.headers or {}).get("content-length")
            if hdr and hdr.isdigit():
                size = int(hdr)
            elif resp.request.resource_type == "document":
                size = len(resp.body())
            else:
                size = 0
        except Exception:
            size = 0
        self.bytes_total += size
        self._bytes_this_question += size

    def _new_page(self, p):
        from medo.browser import launch_browser

        browser = launch_browser(p, headless=self.headless,
                                 args=["--disable-blink-features=AutomationControlled"])
        kwargs: dict = {
            "viewport": {"width": 1280, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            ),
        }
        if has_storage_state(self.storage_state):
            kwargs["storage_state"] = str(self.storage_state)
        context = browser.new_context(**kwargs)
        page = context.new_page()
        page.route("**/*", self._route_interceptor)
        page.on("response", self._on_response)
        return browser, page

    # -- page helpers --------------------------------------------------------

    @staticmethod
    def _wait_for_question(page: Page, timeout_ms: int = 15000) -> bool:
        """Wait until the option labels are in the DOM."""
        try:
            page.wait_for_selector("#label1", timeout=timeout_ms, state="attached")
            return True
        except PlaywrightError:
            return page.locator("#label1").count() > 0

    @staticmethod
    def _parse_visible(page: Page, topic_id: int, page_no: int,
                       submitted: bool = False) -> ParsedQuestion:
        html = page.content()
        return parse_question_html(
            html,
            source_url=topic_url(topic_id),
            source_page=page_no,
            ordinal=page_no,
            submitted=submitted,
        )

    @classmethod
    def _parse_with_retry(cls, page: Page, topic_id: int, page_no: int,
                          max_attempts: int = 4) -> Optional[ParsedQuestion]:
        """Parse after giving the obfuscating JS time to reveal the real stem."""
        for attempt in range(max_attempts):
            q = cls._parse_visible(page, topic_id, page_no)
            if q.stem_text:
                return q
            if page.locator("#label1").count() == 0:
                return None  # no question rendered at all
            page.wait_for_timeout(1200)
        return None

    @classmethod
    def _submit_for_answer(cls, page: Page, topic_id: int, page_no: int) -> Optional[ParsedQuestion]:
        """Fallback: choose option 1 and read the revealed colours."""
        try:
            page.locator("#label1").click(timeout=4000)
            btn = page.locator("#show-b-btn")
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=4000)
                page.wait_for_timeout(700)
            return cls._parse_visible(page, topic_id, page_no, submitted=True)
        except PlaywrightError:
            return None

    @staticmethod
    def _next_state(page: Page) -> str:
        nxt = page.locator('button[data-nav-role="next"]')
        if nxt.count() == 0:
            return "missing"
        try:
            return next_button_state(
                class_attr=nxt.first.get_attribute("class") or "",
                style_attr=nxt.first.get_attribute("style") or "",
                present=True,
            )
        except PlaywrightError:
            return "missing"

    @classmethod
    def _click_next(cls, page: Page) -> bool:
        """Click next and wait until a different question is rendered."""
        before = fingerprint(page.content())
        try:
            page.locator('button[data-nav-role="next"]').first.click(timeout=5000)
        except PlaywrightError:
            return False
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            page.wait_for_timeout(400)
            try:
                if fingerprint(page.content()) != before:
                    page.wait_for_timeout(600)  # let the reveal-JS settle
                    return True
            except PlaywrightError:
                return False
        return False

    def _position_at(self, page: Page, topic_id: int, target: int) -> bool:
        """Land on `target` page of the topic. Verified, never assumed."""
        if target > 1:
            # Fast path: server-side ?page=N — trusted only if verified.
            try:
                page.goto(topic_url(topic_id, page=target), wait_until="domcontentloaded",
                          timeout=45000)
                if self._wait_for_question(page, timeout_ms=10000):
                    cur = read_current_page(page.content())
                    if cur == target:
                        return True
            except PlaywrightError:
                pass
            self._polite_delay()

        # Reliable path: start at page 1 and walk forward through the UI.
        page.goto(topic_url(topic_id), wait_until="domcontentloaded", timeout=45000)
        if not self._wait_for_question(page):
            return False
        for step in range(1, target):
            if self._next_state(page) != "ready" or not self._click_next(page):
                return False  # topic is shorter than the resume target
            self._polite_delay()
        return True

    # -- question persistence ------------------------------------------------

    def _store_assets(self, conn, q: ParsedQuestion) -> None:
        for img in q.images:
            existing = get_asset_by_url(conn, img.original_url)
            if existing:
                img.sha1, img.local_path = existing["sha1"], existing["local_path"]
                continue
            try:
                sha1, path, size, mime = download_asset(
                    img.original_url, assets_dir=self.assets_dir, role=img.role)
                img.sha1, img.local_path, img.bytes_size, img.mime = sha1, path, size, mime
                upsert_asset(conn, sha1, img.original_url, path, mime, size, img.role)
            except Exception as ex:
                print(f"[!] asset download failed ({img.original_url[:80]}): {ex}")

    def _record_question(self, conn, q: ParsedQuestion, topic_id: int) -> int:
        self._store_assets(conn, q)
        qid = upsert_question(conn, q, topic_id=topic_id)
        self._question_times.append(time.monotonic())
        self._question_bytes.append(self._bytes_this_question)
        self._bytes_this_question = 0
        return qid

    def _maybe_print_estimate(self, scraped_count: int) -> None:
        if self._estimate_printed or scraped_count < 20:
            return
        self._estimate_printed = True
        span = self._question_times[-1] - self._question_times[0]
        n = max(1, len(self._question_times) - 1)
        sec_per_q = span / n
        kb_per_q = (sum(self._question_bytes) / n) / 1024
        total_sec = self.elapsed_minutes * 60
        rate = (scraped_count / total_sec) if total_sec > 5 else sec_per_q
        eta_1k = (1000 / rate) / 60 if rate > 0 else float("inf")
        mb_total = self.bytes_total / (1024 * 1024)
        print(
            f"\n[≈] Estimate after {scraped_count} questions: "
            f"{sec_per_q:.1f} s/question, {kb_per_q:.0f} KB/question, "
            f"session total {mb_total:.1f} MB in {self.elapsed_minutes:.1f} min\n"
            f"    → projected {eta_1k:.0f} min per 1000 questions "
            f"at the current pace (incl. polite delays)."
        )

    # -- main loop ------------------------------------------------------------

    def scrape_topic(
        self,
        topic_id: int,
        start_page: Optional[int] = None,
        max_pages: Optional[int] = None,
        refill: bool = False,
    ) -> dict:
        """Scrape one topic; resumes from scrape_state unless start_page is given."""
        self._setup_signal_handlers()
        if self.max_minutes is not None and self._deadline is None:
            self._deadline = self.run_started + self.max_minutes * 60.0

        conn = get_connection(self.db_path)
        init_db(conn)
        state = get_scrape_state(conn, topic_id)

        current_page = start_page if start_page is not None else (
            ((state["last_page"] if state else 0) or 0) + 1
        )
        if refill:
            current_page = start_page if start_page is not None else 1

        update_scrape_state(conn, topic_id=topic_id, last_page=current_page - 1,
                            status="running")
        print(f"[*] Topic {topic_id}: starting at page {current_page}"
              f"{' (refill)' if refill else ''}")

        scraped = 0
        consecutive_errors = 0
        backoff = BACKOFF_BASE
        status = "completed"
        detail = ""

        with sync_playwright() as p:
            browser, page = self._new_page(p)
            try:
                try:
                    if not self._position_at(page, topic_id, current_page):
                        body = page.locator("body").inner_text(timeout=8000)
                        kind = classify_page(body, has_question=False)
                        status = {"limit": "limit", "auth": "auth_required"}.get(kind, "completed")
                        detail = "topic shorter than resume target" if kind == "empty" else kind
                        raise ScraperStopped(status, detail)
                except ScraperStopped:
                    raise
                except PlaywrightError as ex:
                    raise ScraperStopped("error", f"navigation failed: {ex}")

                while True:
                    reason = self._stop_reason(scraped)
                    if reason:
                        raise ScraperStopped(reason)

                    q_started = time.monotonic()
                    self._bytes_this_question = 0

                    try:
                        q = self._parse_with_retry(page, topic_id, current_page)
                    except PlaywrightError as ex:
                        consecutive_errors += 1
                        if consecutive_errors > MAX_CONSECUTIVE_ERRORS:
                            raise ScraperStopped("error", str(ex))
                        time.sleep(backoff)
                        backoff = min(backoff * 2, BACKOFF_MAX)
                        continue

                    if q is None:
                        # No question rendered: classify why, then stop.
                        body = page.locator("body").inner_text(timeout=8000)
                        kind = classify_page(body, has_question=False)
                        if kind == "limit":
                            raise ScraperStopped("limit", "quota/upgrade message detected")
                        if kind == "auth":
                            raise ScraperStopped("auth_required", "session missing or expired")
                        status = "completed"
                        detail = f"no question at page {current_page}"
                        raise ScraperStopped("completed", detail)

                    # Answer fallback (only when the marker did not give it)
                    if needs_submit(q):
                        submitted = self._submit_for_answer(page, topic_id, current_page)
                        if submitted is not None and submitted.stem_text:
                            q = submitted

                    if q.explanation_status == ExplanationStatus.LOCKED:
                        # This page is quota-locked: store what we have, keep
                        # going; a site-wide limit message stops us below.
                        body = page.locator("body").inner_text(timeout=8000)
                        if any(ph in body for ph in LIMIT_PHRASES):
                            update_scrape_state(
                                conn, topic_id=topic_id, last_page=current_page - 1,
                                status="limit", error_message="quota message on page")
                            self._save(conn, topic_id, current_page - 1, "limit")
                            raise ScraperStopped("limit", "quota message detected")

                    qid = self._record_question(conn, q, topic_id)
                    scraped += 1
                    consecutive_errors, backoff = 0, BACKOFF_BASE
                    update_scrape_state(conn, topic_id=topic_id, last_page=current_page,
                                        status="running")
                    if self.on_progress:
                        self.on_progress(current_page, qid, q.stem_text[:40])
                    print(f"[+] topic {topic_id} p{current_page}: "
                          f"{q.stem_text[:50]}... [{q.explanation_status.value}]")

                    if max_pages and scraped >= max_pages:
                        raise ScraperStopped("max_pages")
                    reason = self._stop_reason(scraped)
                    if reason:
                        raise ScraperStopped(reason)
                    self._maybe_print_estimate(scraped)

                    # --- navigation ---
                    nxt_state = self._next_state(page)
                    if nxt_state != "ready":
                        detail = f"next button {nxt_state} at page {current_page}"
                        raise ScraperStopped("completed", detail)
                    if not self._click_next(page):
                        detail = f"next did not advance at page {current_page}"
                        raise ScraperStopped("completed", detail)
                    current_page += 1
                    self._polite_delay()

            except ScraperStopped as stop:
                status, detail = stop.status, stop.message
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        final_last = current_page - 1 if status == "completed" else max(current_page - 1, 0)
        if status in {"completed"}:
            update_scrape_state(conn, topic_id=topic_id, last_page=final_last,
                                total_pages=final_last, status="completed")
        elif status in {"interrupted", "max_minutes", "max_questions", "max_pages"}:
            # last successfully stored page is current_page-1 only if we stored
            # it this round; scrape_state already tracks it — keep stored value.
            row = get_scrape_state(conn, topic_id) or {}
            update_scrape_state(conn, topic_id=topic_id,
                                last_page=row.get("last_page", final_last),
                                status="paused")
        else:  # limit / auth_required / error
            row = get_scrape_state(conn, topic_id) or {}
            update_scrape_state(conn, topic_id=topic_id,
                                last_page=row.get("last_page", final_last),
                                status=status, error_message=detail)

        conn.close()
        if status in {"limit", "auth_required"}:
            self._print_lacking_explanations(db_path=self.db_path)
        return {
            "scraped_count": scraped,
            "last_page": (get_state_last_page(self.db_path, topic_id)),
            "status": status,
            "detail": detail,
            "bytes": self.bytes_total,
            "elapsed_min": round(self.elapsed_minutes, 2),
        }

    def scrape_many(self, topic_ids: list[int], max_pages_per_topic: Optional[int] = None) -> dict:
        """Sequentially scrape several topics; stops on any clean-stop status."""
        totals = {"scraped_count": 0, "topics_done": 0, "stopped": None}
        for tid in topic_ids:
            result = self.scrape_topic(tid, max_pages=max_pages_per_topic)
            totals["scraped_count"] += result["scraped_count"]
            totals["topics_done"] += 1
            if result["status"] in {"interrupted", "max_minutes", "max_questions",
                                    "limit", "auth_required", "error"}:
                totals["stopped"] = {"topic": tid, **result}
                break
        return totals

    def refill_explanations(self, topic_ids: Optional[list[int]] = None) -> dict:
        """Revisit questions whose explanation_status != 'full'.

        Safe to run later (e.g. after a Pro subscription): it only re-reads
        pages the account is shown; it never evades any limit.
        """
        conn = get_connection(self.db_path)
        init_db(conn)
        targets = topic_ids or topics_needing_refill(conn)
        conn.close()
        if not targets:
            print("[+] Nothing to refill — all stored explanations are complete.")
            return {"scraped_count": 0, "topics": []}
        print(f"[*] Refill: {len(targets)} topic(s) need fuller explanations.")
        results = []
        for tid in targets:
            if self._interrupted or (self._deadline and time.monotonic() >= self._deadline):
                break
            r = self.scrape_topic(tid, start_page=1, refill=True)
            results.append(r)
            if r["status"] in {"limit", "auth_required", "error"}:
                break
        return {"scraped_count": sum(r["scraped_count"] for r in results),
                "topics": [r["status"] for r in results]}

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _save(conn, topic_id: int, last_page: int, status: str) -> None:
        update_scrape_state(conn, topic_id=topic_id, last_page=last_page, status=status)

    @staticmethod
    def _print_lacking_explanations(db_path: Path | str) -> None:
        import sqlite3
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT topic_id, count(*) AS n FROM questions
                WHERE explanation_status != 'full'
                GROUP BY topic_id ORDER BY n DESC
            """).fetchall()
            conn.close()
        except Exception:
            return
        if rows:
            print("[i] Topics still lacking full explanations (run "
                  "`medo scrape --refill-explanations` later):")
            for r in rows:
                print(f"    topic {r['topic_id']}: {r['n']} question(s)")


def get_state_last_page(db_path: Path | str, topic_id: int) -> int:
    conn = get_connection(db_path)
    state = get_scrape_state(conn, topic_id)
    conn.close()
    return (state or {}).get("last_page", 0)
