"""
medo/pdf.py
High quality RTL medical test book PDF generator powered by Playwright page.pdf().
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Optional
from jinja2 import Environment, FileSystemLoader

from medo.db import (
    DEFAULT_DB_PATH,
    get_all_questions,
    get_connection,
    get_questions_by_topic,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _embed_local_images(html: str, base_dir: Path) -> str:
    """Replace local image file paths with base64 data URIs so Playwright renders them reliably."""
    import re
    def _repl(match):
        src = match.group(1)
        if src.startswith("data:"):
            return match.group(0)
        p = Path(src)
        if not p.is_absolute():
            p = base_dir / p
        if p.is_file():
            mime, _ = mimetypes.guess_type(str(p))
            mime = mime or "image/png"
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f'src="data:{mime};base64,{b64}"'
        return match.group(0)

    return re.sub(r'src=["\']([^"\']+)["\']', _repl, html)


def render_book_html(
    questions: list[dict],
    title: str = "بانک سوالات پزشکی مدوفست",
    include_answers: bool = True,
    partial: bool = False,
    partial_topics: Optional[set[str]] = None,
) -> str:
    """Render the Jinja2 book template into standalone HTML."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )
    template = env.get_template("book.html")

    # Group by category_path so the book can print section headers per topic
    sections: list[dict] = []
    by_path: dict[str, list[dict]] = {}
    for i, q in enumerate(questions, start=1):
        q["book_no"] = i   # stable global numbering across sections
        key = q.get("category_path") or ""
        by_path.setdefault(key, []).append(q)
    for path, qs in by_path.items():
        tid = qs[0].get("topic_id")
        sections.append({
            "path": path or (f"مبحث {tid}" if tid else ""),
            "questions": qs,
            "partial": bool(partial_topics and tid in partial_topics),
        })

    return template.render(
        title=title,
        questions=questions,
        sections=sections,
        include_answers=include_answers,
        partial=partial,
    )


def export_pdf(
    output_path: Path | str,
    db_path: Path | str = DEFAULT_DB_PATH,
    topic_id: Optional[int] = None,
    title: Optional[str] = None,
    include_answers: bool = True,
    partial: bool = False,
) -> Path:
    """
    Generate a printable PDF test book from the local database.
    Uses Playwright for pixel-perfect RTL rendering.

    partial=True tolerates a half-finished database: topics whose scrape is
    not 'completed' get a printed marker instead of failing the build.
    """
    from playwright.sync_api import sync_playwright
    from medo.browser import launch_browser
    from medo.db import get_scrape_state

    conn = get_connection(db_path)
    if topic_id is not None:
        questions = get_questions_by_topic(conn, topic_id)
        default_title = f"آزمون پزشکی — مبحث {topic_id}"
    else:
        questions = get_all_questions(conn)
        default_title = "بانک جامع سوالات پزشکی"

    partial_topics: set[str] = set()
    if partial:
        for q in questions:
            tid = q.get("topic_id")
            if tid is None:
                continue
            st = get_scrape_state(conn, tid)
            if not st or st.get("status") != "completed":
                partial_topics.add(tid)
    conn.close()

    if not questions:
        raise ValueError("No questions found in database to export.")

    book_title = title or default_title
    raw_html = render_book_html(
        questions, title=book_title, include_answers=include_answers,
        partial=partial, partial_topics=partial_topics)
    final_html = _embed_local_images(raw_html, Path.cwd())

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        page = browser.new_page()

        # Set content with adequate timeout for layout
        page.set_content(final_html, wait_until="networkidle")

        page.pdf(
            path=str(out_file),
            format="A4",
            print_background=True,
            margin={
                "top": "15mm",
                "bottom": "18mm",
                "left": "15mm",
                "right": "15mm",
            },
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=(
                '<div style="width: 100%; font-size: 8pt; text-align: center; color: #a0aec0; direction: rtl; font-family: Tahoma, sans-serif;">'
                'صفحه <span class="pageNumber"></span> از <span class="totalPages"></span>'
                '</div>'
            ),
        )
        browser.close()

    return out_file
