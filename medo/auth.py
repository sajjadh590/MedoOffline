"""
medo/auth.py
Authentication management for medofast.ir using Playwright storage_state.
Never prompts for, handles, or stores the user's password.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

DEFAULT_STORAGE_STATE = Path("data/storage_state.json")


def has_storage_state(path: Path | str = DEFAULT_STORAGE_STATE) -> bool:
    """Check if saved session storage state file exists and is non-empty."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return bool(data.get("cookies") or data.get("origins"))
    except Exception:
        return False


def login_interactive(
    storage_state_path: Path | str = DEFAULT_STORAGE_STATE,
    url: str = "https://medofast.ir/qbank/",
) -> Path:
    """
    Launch a visible browser for the user to log in manually with their credentials.
    Once logged in, saves the session cookies and local storage to storage_state_path.
    """
    from playwright.sync_api import sync_playwright
    from medo.browser import launch_browser

    dest = Path(storage_state_path)
    dest.parent.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print(" [MedoOffline] Manual Login Helper")
    print("=" * 60)
    print(" Opening browser window. Please log into your MedoFast account.")
    print(" Your credentials are never seen or stored by this tool.")
    print(" After successful login, return here and press ENTER.")
    print("=" * 60 + "\n")

    with sync_playwright() as p:
        browser = launch_browser(p, headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(url)

        try:
            input(">>> Press ENTER once you have logged in on the browser window: ")
        except (KeyboardInterrupt, EOFError):
            print("\nLogin cancelled.")
            browser.close()
            return dest

        # Save session state
        context.storage_state(path=str(dest))
        browser.close()

    print(f"\n[+] Login session saved to {dest}")
    return dest
