"""
medo/browser.py
Playwright browser launcher helper that automatically discovers system Chrome or Edge.
Prevents failures in regions where downloading Playwright binaries is blocked or slow.
"""
from __future__ import annotations

from typing import Any
from playwright.sync_api import Browser, Playwright


def launch_browser(p: Playwright, headless: bool = True, **kwargs: Any) -> Browser:
    """
    Launch Chromium browser with automatic fallback:
    1. System Google Chrome (channel='chrome')
    2. System Microsoft Edge (channel='msedge')
    3. Bundled Playwright Chromium
    """
    channels = ["chrome", "msedge", None]
    last_err = None

    for ch in channels:
        try:
            if ch:
                return p.chromium.launch(channel=ch, headless=headless, **kwargs)
            else:
                return p.chromium.launch(headless=headless, **kwargs)
        except Exception as ex:
            last_err = ex
            continue

    if last_err:
        raise last_err
    return p.chromium.launch(headless=headless, **kwargs)
