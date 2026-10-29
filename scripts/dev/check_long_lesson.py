#!/usr/bin/env python
"""Render the synthetic long lesson in real Chrome and fail on clipped layout.

The web UI paints a lesson with ``medo/web/style.css``; a wide table or a long
unbroken token used to push the lesson body to ~3900 px and the box's
``overflow:hidden`` then cut it in half.  A unit test can check the CSS rules,
but only a browser proves the rule wins.  This runs the locally installed
Chrome (no download) on the page built by ``make_long_lesson.py`` at a phone
and a laptop width, reads the measurements the page writes into
``#layoutReport``, saves one screenshot at the table and one at the end, and
exits non-zero if anything overflows.

    python scripts/dev/check_long_lesson.py

Outputs land in ``build/`` (git-ignored): ``long-lesson-<width>.png``.
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / "build"
PAGE = BUILD / "long-lesson.html"

CHROME_CANDIDATES = [
    os.environ.get("CHROME_PATH", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

# Windows clamps a real headless window to ~500px wide, and a screenshot of
# 420 would crop the right-hand (RTL) side of a 500px layout.  500 is the
# narrowest honest width this machine can render.
WIDTHS = (500, 1280)


def find_chrome() -> str | None:
    for path in CHROME_CANDIDATES:
        if path and Path(path).is_file():
            return path
    return shutil.which("chrome") or shutil.which("google-chrome")


def build_page() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import make_long_lesson  # noqa: WPS433 - same directory, dev script

    make_long_lesson.main([])


def run_chrome(chrome: str, width: int, url: str, screenshot: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="medo-chrome-") as profile:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile}",
            f"--window-size={width},900",
            "--virtual-time-budget=4000",
            f"--screenshot={screenshot}",
            "--dump-dom",
            url,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"chrome exited {result.returncode}: "
                + result.stderr.decode("utf-8", "replace")[-500:])
        return result.stdout.decode("utf-8", "replace")


def report_of(dom: str) -> dict:
    match = re.search(r'<pre id="layoutReport"[^>]*>(.*?)</pre>', dom, re.S)
    if not match:
        raise RuntimeError("page did not write #layoutReport (JS did not run)")
    return json.loads(html.unescape(match.group(1)))


def check(report: dict, width: int) -> list[str]:
    problems = []
    if report["lessonChars"] < 30_000:
        problems.append(f"lesson is only {report['lessonChars']} characters")
    if report["docScrollW"] > report["docClientW"] + 1:
        problems.append(
            f"page scrolls sideways: {report['docScrollW']} > {report['docClientW']}")
    if report["bodyScrollW"] > report["bodyClientW"] + 1:
        problems.append(
            f"lesson body overflows: {report['bodyScrollW']} > {report['bodyClientW']}")
    if report["tokenScrollW"] > report["tokenClientW"] + 1:
        problems.append(
            f"unbroken token overflows: {report['tokenScrollW']} > {report['tokenClientW']}")
    if report["tableOverflowX"] != "auto":
        problems.append("wide table does not scroll inside the lesson")
    if report["tableScrollW"] <= report["tableClientW"]:
        problems.append("fixture table is not wider than the lesson column")
    if report["imgWidth"] > report["bodyClientW"] + 1:
        problems.append("image is wider than the lesson column")
    natural = report["imgNatural"].split("x")
    if len(natural) == 2 and int(natural[0]) > 0:
        expected = int(natural[1]) / int(natural[0])
        actual = report["imgHeight"] / max(1, report["imgWidth"])
        if abs(actual - expected) > 0.03:
            problems.append(
                f"image aspect ratio changed: {actual:.2f} vs {expected:.2f}")
    if report["bodyScrollH"] < 3000:
        problems.append("lesson collapsed to a short box (clipped vertically)")
    if "پایان درس آزمایشی" not in report["lastText"]:
        problems.append("the last line of the lesson is missing")
    if report["boxOverflow"] != "hidden":
        problems.append("explanation box no longer clips its rounded corners")
    return [f"{width}px: {p}" for p in problems]


def main() -> int:
    chrome = find_chrome()
    if not chrome:
        print("[!] Chrome not found. Set CHROME_PATH to its executable.")
        return 2
    BUILD.mkdir(parents=True, exist_ok=True)
    build_page()

    problems: list[str] = []
    for width in WIDTHS:
        url = PAGE.as_uri()
        top = BUILD / f"long-lesson-{width}.png"
        dom = run_chrome(chrome, width, url, top)
        report = report_of(dom)
        problems.extend(check(report, width))
        # Second shot: the wide table, so a human can see it scroll internally.
        table_shot = BUILD / f"long-lesson-{width}-table.png"
        run_chrome(chrome, width, url + "#wideTable", table_shot)
        print(f"{width}px: body {report['bodyScrollW']}/{report['bodyClientW']} px, "
              f"table {report['tableScrollW']}/{report['tableClientW']} px, "
              f"page {report['docScrollW']}/{report['docClientW']} px, "
              f"image {report['imgWidth']}x{report['imgHeight']}")
        print(f"          {top.name}, {table_shot.name}")

    if problems:
        print("\nFAIL")
        for line in problems:
            print("  - " + line)
        return 1
    print("\nPASS: no horizontal overflow at " + ", ".join(f"{w}px" for w in WIDTHS))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
