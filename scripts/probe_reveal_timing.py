"""
scripts/probe_reveal_timing.py

Measure how long a topic page takes to expose the real (decrypted) question
stem, and what is visible in the meantime.

Usage:
    python scripts/probe_reveal_timing.py [topic_id] [page_no] [seconds]

Bandwidth: documents + xhr/fetch only, everything else aborted.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from medo.browser import launch_browser
from medo.parser import is_valid_stem_text, parse_question_html

TOPIC_URL = "https://medofast.ir/qbank/topic-id-{topic}"


def main() -> int:
    topic = int(sys.argv[1]) if len(sys.argv) > 1 else 79
    page_no = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    limit = float(sys.argv[3]) if len(sys.argv) > 3 else 60.0

    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        def router(route):
            if route.request.resource_type in {"document", "xhr", "fetch", "script"}:
                route.continue_()
            else:
                route.abort()

        page.route("**/*", router)

        t0 = time.monotonic()
        page.goto(TOPIC_URL.format(topic=topic), wait_until="domcontentloaded", timeout=45000)
        print(f"goto done after {time.monotonic() - t0:.1f}s")

        first_valid = None
        reloads = 0
        while time.monotonic() - t0 < limit:
            html = page.content()
            q = parse_question_html(html)
            ok = bool(q.stem_text and is_valid_stem_text(q.stem_text))
            elapsed = time.monotonic() - t0
            body = page.locator("body").inner_text(timeout=8000)
            blocked = "پشتیبانی نمی شود" in body or "پشتیبانی نمی‌شود" in body
            print(f"[{elapsed:5.1f}s] valid={ok!s:5} blocked={blocked!s:5} "
                  f"label1={page.locator('#label1').count()} "
                  f"stem={q.stem_text[:60]!r}")
            if ok:
                first_valid = elapsed
                break
            # Try a reload once the interstitial is clearly settled.
            if reloads < 2 and elapsed > 4 + reloads * 6:
                reloads += 1
                print(f"    --> reload #{reloads}")
                page.reload(wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1000)

        print()
        print("first valid stem after:", f"{first_valid:.1f}s" if first_valid else "never")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
