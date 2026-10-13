"""
scripts/probe_live.py
Minimal live probe (a handful of requests, images/fonts/CSS-blocked) to answer:
  1. Does the topic page load without a login session?
  2. How does pagination actually work — XHR/JSON endpoint, URL query, or UI click?
  3. Is the stem/options/answer parseable from the live DOM?

Bandwidth: documents + fetch/xhr only; everything else aborted.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from medo.browser import launch_browser

URL = "https://medofast.ir/qbank/topic-id-79"


def main() -> int:
    log: list[str] = []

    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        def on_request(req):
            if req.resource_type in {"document", "xhr", "fetch"}:
                log.append(f"REQ  {req.method:6} {req.resource_type:9} {req.url}")

        def on_response(resp):
            if resp.request.resource_type in {"document", "xhr", "fetch"}:
                ctype = (resp.headers or {}).get("content-type", "")
                log.append(f"RESP {resp.status:3} {resp.request.resource_type:9} {ctype[:40]} {resp.url}")

        page.on("request", on_request)
        page.on("response", on_response)

        # Block everything except documents and XHR/fetch (bandwidth guard)
        def router(route):
            if route.request.resource_type in {"document", "xhr", "fetch"}:
                route.continue_()
            else:
                route.abort()

        page.route("**/*", router)

        resp = page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        print("initial status:", resp.status if resp else "?")
        page.wait_for_timeout(3000)  # give obfuscating JS time to reveal the real stem

        body = page.locator("body").inner_text(timeout=10000)
        print("login wall:", "ورود به حساب کاربری" in body and "#label1" not in page.content())
        print("has label1:", page.locator("#label1").count(),
              "| has qtxt:", page.locator("#qtxt").count(),
              "| has show-b-btn:", page.locator("#show-b-btn").count())

        # --- pagination discovery ---
        nxt = page.locator('button[data-nav-role="next"]')
        print("next button count:", nxt.count())
        if nxt.count():
            print("next data-page attr:", nxt.first.get_attribute("data-page"),
                  "| disabled class:", nxt.first.get_attribute("class"))
            url_before = page.url
            html_before = page.content()
            log.append("--- CLICK NEXT ---")
            nxt.first.click()
            page.wait_for_timeout(3500)
            html_after = page.content()
            print("url before/after:", url_before, "->", page.url)
            print("dom changed:", html_before != html_after)
            # did the question actually change? compare option-3 base64 text
            import re
            opts = re.findall(r'data-encoded-text="([^"]+)"', html_after)
            opts_before = re.findall(r'data-encoded-text="([^"]+)"', html_before)
            print("options before:", opts_before[:4])
            print("options after :", opts[:4])
            print("page indicator attrs:", re.findall(r'data-page="[^"]*"', html_after)[:6])

        print("\n=== network log ===")
        for line in log:
            print(" ", line)

        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
