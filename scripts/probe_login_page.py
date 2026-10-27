"""Does /login render the real SMS form now? (read-only)"""
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
        r = page.goto("https://medofast.ir/login?next=/dashboard/welcome",
                      wait_until="domcontentloaded", timeout=45000)
        time.sleep(3)
        html = page.content()
        print("status:", r.status if r else None, "| url:", page.url)
        print("title:", page.title())
        print("inputs:", re.findall(r"<input[^>]*>", html)[:6])
        print("has phone word:", "موبایل" in html or "شماره" in html)
        body = re.sub(r"\s+", " ", page.inner_text("body"))[:400]
        print("body:", body)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
