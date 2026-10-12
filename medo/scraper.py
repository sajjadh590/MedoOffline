"""
medo/scraper.py
Polite, resumable Playwright harvester for medofast.ir question bank.
"""
from __future__ import annotations

import random
import re
import signal
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import BrowserContext, Page, sync_playwright

from medo.assets import download_asset
from medo.auth import DEFAULT_STORAGE_STATE, has_storage_state
from medo.db import (
    DEFAULT_DB_PATH,
    get_asset_by_url,
    get_connection,
    get_scrape_state,
    init_db,
    update_scrape_state,
    upsert_asset,
    upsert_question,
)
from medo.parser import parse_question_html

# Blocklist patterns for Playwright request interception
BLOCKED_PATTERNS = [
    re.compile(r"yektanet", re.I),
    re.compile(r"mediaad", re.I),
    re.compile(r"google-analytics", re.I),
    re.compile(r"googletagmanager", re.I),
    re.compile(r"clarity\.ms", re.I),
    re.compile(r"hotjar", re.I),
    re.compile(r"\.(woff2?|ttf|otf|eot)(\?.*)?$", re.I),  # save bandwidth on repeated font downloads
]


def _route_interceptor(route):
    """Intercept and abort ad, analytics, and non-essential tracker requests."""
    url = route.request.url
    for pat in BLOCKED_PATTERNS:
        if pat.search(url):
            route.abort()
            return
    route.continue_()


class ScraperInterruptedException(Exception):
    """Raised when SIGINT or SIGTERM is caught."""
    pass


class Harvester:
    """
    Polite question scraper.
    Guarantees 1 request at a time, polite delay, clean interrupt handling,
    and automatic state tracking in SQLite.
    """

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        storage_state: Path | str = DEFAULT_STORAGE_STATE,
        delay_range: tuple[float, float] = (1.5, 3.0),
        headless: bool = True,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.storage_state = Path(storage_state)
        self.delay_min, self.delay_max = delay_range
        self.headless = headless
        self.on_progress = on_progress
        self._interrupted = False

    def _setup_signal_handlers(self):
        def _handler(sig, frame):
            print("\n[!] Graceful stop requested. Finishing current question before exiting...")
            self._interrupted = True

        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)

    def scrape_topic(
        self,
        topic_id: int,
        start_page: Optional[int] = None,
        max_pages: Optional[int] = None,
    ) -> dict:
        """
        Scrape questions for a given topic ID (e.g. topic-id-79).
        Resumes from last page saved in scrape_state unless start_page is explicit.
        """
        self._setup_signal_handlers()

        conn = get_connection(self.db_path)
        init_db(conn)

        # Check existing progress
        state = get_scrape_state(conn, topic_id)
        if start_page is None:
            current_page = (state["last_page"] + 1) if state else 1
        else:
            current_page = start_page

        update_scrape_state(conn, topic_id=topic_id, last_page=current_page - 1, status="running")

        scraped_count = 0
        consecutive_errors = 0
        backoff_sec = 5.0

        from medo.browser import launch_browser

        with sync_playwright() as p:
            launch_args = ["--disable-blink-features=AutomationControlled"]
            browser = launch_browser(p, headless=self.headless, args=launch_args)

            context_kwargs = {
                "viewport": {"width": 1280, "height": 900},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
                ),
            }
            if has_storage_state(self.storage_state):
                context_kwargs["storage_state"] = str(self.storage_state)

            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            page.route("**/*", _route_interceptor)

            while True:
                if self._interrupted:
                    update_scrape_state(conn, topic_id=topic_id, last_page=current_page - 1, status="paused")
                    print(f"\n[+] Paused cleanly at page {current_page - 1}. Resumable anytime.")
                    break

                if max_pages and scraped_count >= max_pages:
                    update_scrape_state(conn, topic_id=topic_id, last_page=current_page - 1, status="completed")
                    print(f"\n[+] Reached requested max_pages limit ({max_pages}).")
                    break

                url = f"https://medofast.ir/qbank/topic-id-{topic_id}?page={current_page}"
                print(f"[*] Fetching topic {topic_id}, page {current_page}...")

                try:
                    resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    status_code = resp.status if resp else 200

                    # Check for rate limiting / server errors
                    if status_code in (429, 503):
                        print(f"[!] HTTP {status_code} received. Exponential backoff ({backoff_sec:.1f}s)...")
                        time.sleep(backoff_sec)
                        backoff_sec = min(backoff_sec * 2, 120.0)
                        consecutive_errors += 1
                        if consecutive_errors > 5:
                            update_scrape_state(
                                conn, topic_id=topic_id, last_page=current_page - 1,
                                status="rate_limited", error_message=f"HTTP {status_code}"
                            )
                            break
                        continue

                    # Reset error backoff on successful load
                    consecutive_errors = 0
                    backoff_sec = 5.0

                    # Check for CAPTCHA or Login required
                    body_text = page.locator("body").inner_text()
                    if "کپچا" in body_text or "captcha" in body_text.lower():
                        print("[!] CAPTCHA challenge detected! Stopping scraper to prevent account ban.")
                        update_scrape_state(
                            conn, topic_id=topic_id, last_page=current_page - 1,
                            status="captcha", error_message="CAPTCHA detected"
                        )
                        break

                    if "ورود به حساب کاربری" in body_text and current_page > 1:
                        print("[!] Session expired. Please re-run 'medo login'.")
                        update_scrape_state(
                            conn, topic_id=topic_id, last_page=current_page - 1,
                            status="auth_required", error_message="Session expired"
                        )
                        break

                    # Check if question stem is present
                    has_stem = page.locator("#q-stem-container").count() > 0 or page.locator("#q-stem").count() > 0
                    if not has_stem:
                        print(f"[*] No question found on page {current_page}. Topic may be completed.")
                        update_scrape_state(
                            conn, topic_id=topic_id, last_page=current_page - 1,
                            total_pages=current_page - 1, status="completed"
                        )
                        break

                    # 1. Parse initial page state (before submit)
                    html_before = page.content()
                    q = parse_question_html(
                        html_before,
                        source_url=url,
                        source_page=current_page,
                        ordinal=current_page,
                        submitted=False,
                    )

                    # 2. If correct_index could not be deduced from marker, submit option 1 to reveal color
                    if q.correct_index is None:
                        opt1_btn = page.locator("#label1")
                        if opt1_btn.count() > 0:
                            opt1_btn.click()
                            submit_btn = page.locator("#show-b-btn")
                            if submit_btn.count() > 0 and submit_btn.is_visible():
                                submit_btn.click()
                                page.wait_for_timeout(600)  # brief wait for color styles to apply
                                html_after = page.content()
                                q = parse_question_html(
                                    html_after,
                                    source_url=url,
                                    source_page=current_page,
                                    ordinal=current_page,
                                    submitted=True,
                                )

                    # 3. Download images (mindmaps, question images)
                    for img in q.images:
                        existing = get_asset_by_url(conn, img.original_url)
                        if existing:
                            img.sha1 = existing["sha1"]
                            img.local_path = existing["local_path"]
                        else:
                            try:
                                sha1, path, size, mime = download_asset(
                                    img.original_url,
                                    role=img.role,
                                )
                                img.sha1 = sha1
                                img.local_path = path
                                img.bytes_size = size
                                img.mime = mime
                                upsert_asset(conn, sha1, img.original_url, path, mime, size, img.role)
                            except Exception as ex:
                                print(f"[!] Warning: failed to download asset {img.original_url}: {ex}")

                    # 4. Save question to database
                    qid = upsert_question(conn, q, topic_id=topic_id)
                    scraped_count += 1
                    update_scrape_state(conn, topic_id=topic_id, last_page=current_page, status="running")

                    if self.on_progress:
                        self.on_progress(current_page, qid, q.stem_text[:40])

                    # 5. Polite random delay
                    delay = random.uniform(self.delay_min, self.delay_max)
                    time.sleep(delay)
                    current_page += 1

                except Exception as ex:
                    print(f"[!] Error on page {current_page}: {ex}")
                    consecutive_errors += 1
                    if consecutive_errors > 3:
                        update_scrape_state(
                            conn, topic_id=topic_id, last_page=current_page - 1,
                            status="error", error_message=str(ex)
                        )
                        break
                    time.sleep(backoff_sec)

            browser.close()

        conn.close()
        return {"scraped_count": scraped_count, "last_page": current_page - 1}
