#!/usr/bin/env python
"""Build a deliberately hostile lesson and a standalone page that renders it.

The bug it reproduces: one very long explanatory answer made half the page
disappear.  The fixture is synthetic — no site text, no captures — and covers
the four ways a lesson can escape its container:

* 30,000+ characters of ordinary Persian prose,
* a table far wider than any phone or laptop,
* one unbroken 500-character token (a URL-like blob),
* a large image with fixed width/height attributes,
* deeply nested lists.

    python scripts/dev/make_long_lesson.py [--out build/long-lesson.html]

It writes a page that inlines the real ``medo/web/style.css`` and the real
question-card markup, so opening it shows exactly what the app would show and a
browser check can measure it.  ``tests/test_long_lesson.py`` imports the same
builder, so the test and the page cannot drift apart.
"""
from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "medo" / "web" / "style.css"

# A 1200x800 SVG stands in for a large uploaded diagram: it needs no network.
IMAGE = (
    "data:image/svg+xml;charset=utf-8,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' width='1200' height='800'%3E"
    "%3Crect width='1200' height='800' fill='%23dbe7f0'/%3E"
    "%3Ccircle cx='300' cy='240' r='160' fill='%236aa9d8'/%3E"
    "%3Ctext x='60' y='560' font-size='64' fill='%23223'%3Esynthetic diagram%3C/text%3E"
    "%3C/svg%3E"
)


def synthetic_lesson(min_chars: int = 30_000) -> str:
    """The hostile lesson body exactly as the site HTML would hand it over."""
    parts = ["<h4>درس‌نامهٔ آزمایشی</h4>"]
    paragraph = (
        "بیمار با تب و لرز و درد پهلو مراجعه کرده است؛ در بررسی اولیه علائم "
        "حیاتی ناپایدار و شمارش گلبول سفید بالا گزارش شد. "
    )
    while sum(len(part) for part in parts) < min_chars:
        parts.append(f"<p>{html.escape(paragraph)}</p>")

    rows = []
    for r in range(1, 13):
        cells = "".join(
            f"<td>ردیف {r} ستون {c} — مقدار طولانی برای پهن کردن جدول</td>"
            for c in range(1, 25))
        rows.append(f"<tr>{cells}</tr>")
    parts.append(
        "<p>جدول زیر عمداً از عرض صفحهٔ گوشی و لپ‌تاپ پهن‌تر است:</p>"
        "<table id=\"wideTable\"><thead><tr>"
        + "".join(f"<th>ستون {c}</th>" for c in range(1, 25))
        + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>")

    blob = "https://example.invalid/" + ("x" * 470) + "/پایان"
    assert len(blob) >= 500
    parts.append(f"<p>پیوند بدون فاصله: {html.escape(blob)}</p>")

    parts.append(
        f"<p><img src=\"{IMAGE}\" width=\"1200\" height=\"800\" "
        "alt=\"نمودار آزمایشی\"></p>")

    nested = ""
    for level in range(1, 11):
        nested += f"<ul><li>سطح {level}"
    parts.append("<p>فهرست تودرتو:</p>" + nested + "پایان" + "</li></ul>" * 10)
    return "".join(parts)


def _inline_css() -> str:
    # The real stylesheet; only its @import/@font-face-free nature matters.
    return CSS.read_text(encoding="utf-8")


def standalone_html(lesson: str | None = None) -> str:
    """A page with the real app shell around the synthetic lesson."""
    body = lesson if lesson is not None else synthetic_lesson()

    head = """<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>آزمون درس‌نامهٔ بلند</title>
<style>
"""
    middle = """
</style>
</head>
<body class="theme-dark">
<div class="navbar glass">
  <div class="nav-brand"><span class="brand-title">Medo — آزمون چیدمان</span></div>
</div>
<div class="app-layout">
  <main class="viewer-container">
    <div class="question-card glass" id="questionCard">
      <div class="card-top-bar"><span>سوال آزمایشی</span><span>۱ از ۱</span></div>
      <div class="question-text" id="stemContent">
        <p>این صفحه فقط برای آزمودن چیدمان یک درس‌نامهٔ بسیار بلند است.</p>
      </div>
      <div class="options-container" id="optionsContainer">
        <div class="option-box"><div class="option-left">
          <div class="option-letter">۱</div>
          <div class="option-text">گزینهٔ آزمایشی</div>
        </div></div>
      </div>
      <div class="explanation-box" id="explanationBox">
        <div class="explanation-header" id="explanationHeader">
          <div class="expl-title-wrap">پاسخ تشریحی</div>
          <span class="chevron-icon">⌄</span>
        </div>
        <div class="explanation-body" id="explanationContent">
"""
    tail = """
          <p id="endMarker">پایان درس آزمایشی</p>
        </div>
      </div>
    </div>
  </main>
</div>
<script>
/* Fixture only: headless Chrome cannot click, so the page measures itself.
   check_long_lesson.py reads #layoutReport and fails on horizontal overflow. */
window.addEventListener('load', function () {
  const body = document.getElementById('explanationContent');
  const table = body.querySelector('table');
  const img = body.querySelector('img');
  const token = Array.prototype.slice.call(body.querySelectorAll('p'))
    .filter(function (p) { return p.textContent.indexOf('example.invalid') >= 0; })[0];
  const box = document.getElementById('explanationBox');
  const rect = img.getBoundingClientRect();
  const report = {
    viewport: innerWidth,
    docScrollW: document.documentElement.scrollWidth,
    docClientW: document.documentElement.clientWidth,
    bodyScrollW: body.scrollWidth,
    bodyClientW: body.clientWidth,
    bodyScrollH: body.scrollHeight,
    boxOverflow: getComputedStyle(box).overflow,
    tableScrollW: table.scrollWidth,
    tableClientW: table.clientWidth,
    tableOverflowX: getComputedStyle(table).overflowX,
    tokenScrollW: token.scrollWidth,
    tokenClientW: token.clientWidth,
    imgWidth: Math.round(rect.width),
    imgHeight: Math.round(rect.height),
    imgNatural: img.naturalWidth + 'x' + img.naturalHeight,
    lessonChars: body.textContent.length,
    lastText: body.textContent.trim().slice(-40)
  };
  const pre = document.createElement('pre');
  pre.id = 'layoutReport';
  /* Hidden: a long one-line <pre> would stretch the page and, in RTL, make
     Chrome screenshot the empty far-left end instead of the lesson. */
  pre.style.display = 'none';
  pre.textContent = JSON.stringify(report);
  document.body.appendChild(pre);
});
</script>
</body>
</html>
"""
    return head + _inline_css() + middle + body + tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "build" / "long-lesson.html")
    args = parser.parse_args(argv)
    lesson = synthetic_lesson()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(standalone_html(lesson), encoding="utf-8")
    print(f"lesson: {len(lesson):,} chars of HTML")
    print(f"page:   {args.out}  ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
