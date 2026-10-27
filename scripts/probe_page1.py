"""
scripts/probe_page1.py

The site sometimes answers the topic URL with a DDoS/login redirect.  This
probe checks whether re-navigating recovers the real question page, under the
exact harvester conditions (same routing, same user-agent).

Usage:
    python scripts/probe_page1.py [topic_id] [attempts]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from medo.parser import is_valid_stem_text, parse_question_html
from medo.scraper import Harvester, topic_url


def _describe(page) -> str:
    html = page.content()
    q = parse_question_html(html)
    ok = bool(q.stem_text and is_valid_stem_text(q.stem_text))
    return (f"url={page.url.split('?')[0]:<48} label1={page.locator('#label1').count()} "
            f"valid_stem={ok!s:5} stem={q.stem_text[:45]!r}")


def main() -> int:
    topic = int(sys.argv[1]) if len(sys.argv) > 1 else 79
    attempts = int(sys.argv[2]) if len(sys.argv) > 2 else 4

    h = Harvester()
    with sync_playwright() as p:
        browser, page = h._new_page(p)
        for attempt in range(1, attempts + 1):
            t0 = time.monotonic()
            page.goto(topic_url(topic), wait_until="domcontentloaded", timeout=45000)
            # Let the client-side app boot (the site redirects via JS first).
            for _ in range(8):
                page.wait_for_timeout(1500)
                if page.locator("#label1").count() > 0:
                    break
            print(f"attempt {attempt} ({time.monotonic() - t0:.1f}s): {_describe(page)}")
            if page.locator("#label1").count() > 0:
                q = parse_question_html(page.content())
                if q.stem_text and is_valid_stem_text(q.stem_text):
                    print("\nREAL QUESTION PAGE after", attempt, "attempt(s)")
                    print("stem:", q.stem_text[:120])
                    browser.close()
                    return 0
        print("\nnever reached the question on this topic")
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
