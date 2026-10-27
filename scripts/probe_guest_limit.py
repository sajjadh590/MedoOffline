"""Where exactly does the guest wall start? Probe pages 2, 5, 20, 29, 30, 31."""
from __future__ import annotations

import re
import sys
import time

sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright

from medo.browser import launch_browser, new_context


def main() -> int:
    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        ctx = new_context(browser)
        page = ctx.new_page()
        for n in (2, 5, 20, 29, 30, 31):
            url = f"https://medofast.ir/qbank/topic-id-83?page={n}"
            r = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(2.2)
            html = page.content()
            opts = len(re.findall(r'data-encoded-text="', html))
            print(f"page {n}: status={r.status if r else None} opts={opts} "
                  f"url={page.url}")
            if opts == 0:
                try:
                    body = re.sub(r"\s+", " ", page.inner_text("body"))[:160]
                except Exception:
                    body = ""
                print("   ", body)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
