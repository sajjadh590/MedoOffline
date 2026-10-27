"""Compare browser fingerprints against the site's 403 page.

For each mode, open the topic page fresh and report status/title and whether
questions render. Read-only.
"""
from __future__ import annotations

import re
import sys
import time

sys.path.insert(0, ".")

from playwright.sync_api import sync_playwright

from medo.browser import launch_browser

URL = "https://medofast.ir/qbank/topic-id-83?page=1"

STEALTH = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['fa-IR', 'fa', 'en-US', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
const origQuery = window.navigator.permissions && window.navigator.permissions.query;
if (origQuery) {
  window.navigator.permissions.query = (p) => (
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : origQuery(p));
}
"""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")


def run(p, label: str, headless: bool, ua: str | None, stealth: bool) -> None:
    kwargs: dict = {}
    if headless:
        kwargs["args"] = ["--disable-blink-features=AutomationControlled"]
        kwargs["ignore_default_args"] = ["--enable-automation"]
    browser = launch_browser(p, headless=headless, **kwargs)
    ctx_kwargs: dict = {}
    if ua:
        ctx_kwargs["user_agent"] = ua
        ctx_kwargs["locale"] = "fa-IR"
        ctx_kwargs["extra_http_headers"] = {"Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8"}
    ctx = browser.new_context(**ctx_kwargs)
    if stealth:
        ctx.add_init_script(STEALTH)
    page = ctx.new_page()
    ua_live = page.evaluate("navigator.userAgent")
    wd = page.evaluate("navigator.webdriver")
    try:
        r = page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        status = r.status if r else None
    except Exception as ex:
        status = f"ERR {type(ex).__name__}"
    time.sleep(4)
    html = page.content()
    txt = re.sub(r"\s+", " ", page.title())
    body = ""
    try:
        body = re.sub(r"\s+", " ", page.inner_text("body"))[:200]
    except Exception:
        pass
    print(f"\n### {label}: status={status} title={txt!r}")
    print(f"    ua={ua_live[:70]!r} webdriver={wd}")
    print(f"    opts={len(re.findall(r'data-encoded-text=', html))} "
          f"cookies={sorted({c['name'] for c in ctx.cookies()})}")
    print(f"    body={body!r}")
    browser.close()


def main() -> int:
    with sync_playwright() as p:
        run(p, "A headless/default UA", True, None, False)
        run(p, "B headless/real UA+stealth", True, UA, True)
        run(p, "C headed/default UA", False, None, False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
