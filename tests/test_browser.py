"""
tests/test_browser.py
Unit tests for medo/browser.py: system browser detection and launch.
"""
from playwright.sync_api import sync_playwright
from medo.browser import launch_browser


def test_launch_browser():
    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        assert browser.is_connected()
        page = browser.new_page()
        page.set_content("<html dir='rtl'><body>آزمون پزشکی</body></html>")
        text = page.locator("body").inner_text()
        assert "آزمون پزشکی" in text
        browser.close()
