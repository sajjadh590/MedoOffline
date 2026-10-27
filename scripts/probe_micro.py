"""
scripts/probe_micro.py
Does a micro-topic (`/qbank/topic-id-N?tag=T`) page work like a topic page:
a real question, a next button with data-page, and does clicking next stay
inside the tag filter?  A handful of document requests only.

Usage: python scripts/probe_micro.py [topic_id] [tag_id]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from medo.auth import DEFAULT_STORAGE_STATE, has_storage_state
from medo.browser import launch_browser, new_context


def main() -> int:
    topic = sys.argv[1] if len(sys.argv) > 1 else "94"
    tag = sys.argv[2] if len(sys.argv) > 2 else "1781"
    url = f"https://medofast.ir/qbank/topic-id-{topic}?tag={tag}"

    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        kwargs = {}
        if has_storage_state(DEFAULT_STORAGE_STATE):
            kwargs["storage_state"] = str(DEFAULT_STORAGE_STATE)
        ctx = new_context(browser, **kwargs)
        page = ctx.new_page()

        def router(route):
            if route.request.resource_type in {"document", "xhr", "fetch",
                                              "script", "stylesheet"}:
                route.continue_()
            else:
                route.abort()

        page.route("**/*", router)
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        print("status:", resp.status if resp else "?")
        print("final url:", page.url)
        print("label1:", page.locator("#label1").count())
        html = page.content()
        body = page.locator("body").inner_text(timeout=10000)
        print("login wall:", "ورود به حساب کاربری" in body and "#label1" not in html)
        opts = re.findall(r'data-encoded-text="([^"]+)"', html)
        print("options:", len(opts), opts[:2])

        nxt = page.locator('button[data-nav-role="next"]')
        print("next count:", nxt.count())
        if nxt.count():
            print("next data-page:", nxt.first.get_attribute("data-page"))
            nxt.first.click()
            page.wait_for_timeout(3500)
            print("after click url:", page.url)
            html2 = page.content()
            opts2 = re.findall(r'data-encoded-text="([^"]+)"', html2)
            print("options after:", len(opts2), opts2[:2])
            print("changed:", html != html2)
            print("still tagged:", "tag=" in page.url)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
