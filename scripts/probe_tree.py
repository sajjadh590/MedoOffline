"""Quick live probe: which pages expose /qbank/cat-id links, and does a
topic page still render questions?  Prints PASS/FAIL markers only."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from medo.scraper import (  # noqa: E402
    LOGIN_PHRASES,
    Harvester,
    extract_menu_links,
)
from playwright.sync_api import sync_playwright  # noqa: E402

CAT_RE = re.compile(r"/qbank/cat-id-(\d+)")


def main() -> int:
    harvester = Harvester(headless=True, fast=True)
    paths = ["/main-menu", "/qbank-keshvari", "/qbank/", "/qbank-talifi"]
    ok_links = False
    with sync_playwright() as p:
        browser, page = harvester._new_page(p)
        try:
            for path in paths:
                url = "https://medofast.ir" + path
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    page.wait_for_timeout(1500)
                except Exception as ex:
                    print(f"[!] goto {path} failed: {ex}")
                    continue
                html = page.content()
                links = extract_menu_links(html)
                cats = [l for l in links if l["kind"] == "cat"]
                topics = [l for l in links if l["kind"] == "topic"]
                body = page.locator("body").inner_text(timeout=8000)
                is_login = any(ph in body for ph in LOGIN_PHRASES) or "/login" in page.url
                print(f"[PROBE] {path}: url={page.url} links={len(links)} "
                      f"cats={len(cats)} topics={len(topics)} login_wall={is_login}")
                if cats:
                    ok_links = True
                    for c in cats[:5]:
                        print(f"    cat: {c['title']} -> {c['href']}")
                if not links:
                    # Show any qbank-ish anchors at all for diagnosis
                    raw = re.findall(r'href="([^"]*qbank[^"]*)"', html)
                    print(f"    raw qbank hrefs ({len(set(raw))}): {sorted(set(raw))[:10]}")

            # topic render check
            page.goto("https://medofast.ir/qbank/topic-id-79?page=1",
                      wait_until="domcontentloaded", timeout=45000)
            has_label = page.locator("#label1").count() > 0
            page.wait_for_timeout(2500)
            has_label = has_label or page.locator("#label1").count() > 0
            body = page.locator("body").inner_text(timeout=8000)
            is_login = any(ph in body for ph in LOGIN_PHRASES) or "/login" in page.url
            nxt = page.locator('button[data-nav-role="next"]').count()
            print(f"[PROBE] topic-79 p1: url={page.url} label1={has_label} "
                  f"next_btn={nxt} login_wall={is_login}")
        finally:
            try:
                browser.close()
            except Exception:
                pass

    print(f"PROBE_LINKS_OK: {ok_links}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
