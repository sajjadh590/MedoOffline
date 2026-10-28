"""
medo/pdf.py
High quality RTL medical test book PDF generator powered by Playwright page.pdf().
"""
from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Optional
from jinja2 import Environment, FileSystemLoader

from medo.db import (
    DEFAULT_DB_PATH,
    build_topic_paths,
    get_all_questions,
    get_category_tree,
    get_connection,
    get_questions_by_topic,
)
from medo.paths import DATA_DIR, PROJECT_ROOT

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Don't trust the OS mime database: this Windows install maps .webp to
# something else, and a WebP payload labelled image/png can fail to render.
_IMAGE_MIME = {
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}


def _embed_local_images(html: str, base_dir: Path, *fallback_dirs: Path) -> str:
    """Replace local image file paths with base64 data URIs so Playwright renders them reliably.

    Stored markup points at ``assets/<file>`` (the layout the web export
    uses).  For the PDF that path lives under ``data/``, so the extra base
    directories are tried before giving up.
    """
    import re

    def _repl(match):
        src = match.group(1)
        if src.startswith("data:"):
            return match.group(0)
        p = Path(src)
        candidates = [p if p.is_absolute() else base_dir / p]
        candidates += [d / p for d in fallback_dirs if not p.is_absolute()]
        p = next((c for c in candidates if c.is_file()), None)
        if p is not None:
            mime = (_IMAGE_MIME.get(p.suffix.lower())
                    or mimetypes.guess_type(str(p))[0]
                    or "image/png")
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f'src="data:{mime};base64,{b64}"'
        return match.group(0)

    return re.sub(r'src=["\']([^"\']+)["\']', _repl, html)


def assign_sections(
    questions: list[dict],
    tree: list[dict],
    node_paths: dict[str, str],
) -> dict[str, int]:
    """Give every question its section path and return the print order.

    A question downloaded through a subtopic (``?tag=``) is filed under that
    subtopic's full path; everything else under its topic.  The returned map
    (section path → position in a depth-first walk of the tree) is what makes
    the book and its فهرست follow the site hierarchy instead of topic-id order.
    """
    order: dict[str, int] = {}

    def _walk(nodes: list[dict]) -> None:
        for node in nodes:
            order[node["source_url"]] = len(order)
            _walk(node.get("children", []))

    _walk(tree)

    section_order: dict[str, int] = {}
    for q in questions:
        tid = q.get("topic_id")
        if tid is None:
            q["book_path"] = "سایر سوالات"
            continue
        tag = q.get("tag_id")
        url = f"/qbank/topic-id-{int(tid)}"
        if tag is not None:
            url += f"?tag={int(tag)}"
        path = node_paths.get(url) or q.get("category_path") or ""
        if tag is not None and q.get("subtopic_title"):
            title_ = q["subtopic_title"].strip()
            if title_ and not path.endswith(title_):
                path = (path + " » " if path else "") + title_
        if not path:
            path = f"مبحث {tid}"
        q["book_path"] = path
        pos = order.get(url)
        if pos is not None:
            section_order[path] = min(section_order.get(path, pos), pos)
    return section_order


def render_book_html(
    questions: list[dict],
    title: str = "بانک سوالات پزشکی مدوفست",
    include_answers: bool = True,
    partial: bool = False,
    partial_topics: Optional[set[str]] = None,
    section_order: Optional[dict[str, int]] = None,
) -> str:
    """Render the Jinja2 book template into standalone HTML.

    Sections follow the category hierarchy: one section per topic, and a
    nested section for each downloaded subtopic (``book_path`` is set by
    :func:`export_pdf` from the stored tree).  ``section_order`` maps a
    section path to its position in the tree so the book prints in the same
    order as the sidebar, not in topic-id order.
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=True,
    )

    def key_badge(html: str) -> str:
        """Wrap the site's «تایید گزینه N» marker as a printed key badge."""
        return re.sub(
            r"(تایید|تأیید)\s*گزینه\s*([0-9۰-۹]+)",
            r'<span class="answer-key-badge">\g<0></span>',
            str(html or ""),
        )

    env.filters["key_badge"] = key_badge
    template = env.get_template("book.html")

    sections: list[dict] = []
    by_path: dict[str, list[dict]] = {}
    for q in questions:
        key = q.get("book_path") or q.get("category_path") or ""
        if not key:
            tid = q.get("topic_id")
            key = f"مبحث {tid}" if tid else "سایر سوالات"
        by_path.setdefault(key, []).append(q)

    for path, qs in by_path.items():
        tid = qs[0].get("topic_id")
        sections.append({
            "path": path,
            "questions": qs,
            "partial": bool(partial_topics and tid in partial_topics),
            # Answer key/explanation notes are printed per section, next to the
            # questions they belong to, instead of one block at the end.
            "missing_answers": sum(1 for q in qs
                                   if q.get("correct_index") in (None, 0)),
            "missing_explanations": sum(
                1 for q in qs
                if not (q.get("explanation_html") or q.get("explanation_text"))),
        })
    if section_order:
        sections.sort(key=lambda s: section_order.get(s["path"], len(section_order)))

    # Number questions *after* ordering: the book reads section by section, so
    # question numbers (and the answer key, which uses the same sequence) must
    # run 1..N in printed order, not in database insertion order.
    ordered_questions: list[dict] = []
    for q in [q for sec in sections for q in sec["questions"]]:
        q["book_no"] = len(ordered_questions) + 1
        ordered_questions.append(q)

    # Table of contents: numbered sections, indented by tree depth.  Counts are
    # printed because they are exact; page numbers are not available from
    # Chromium's pager, so none are invented.
    depths = [s["path"].count("»") for s in sections] or [0]
    base_depth = min(depths)
    toc = []
    for no, sec in enumerate(sections, start=1):
        sec["no"] = no
        toc.append({
            "no": no,
            "path": sec["path"],
            "count": len(sec["questions"]),
            "depth": max(0, sec["path"].count("»") - base_depth),
        })

    missing_explanations = sum(
        1 for q in questions
        if not (q.get("explanation_html") or q.get("explanation_text"))
    )

    return template.render(
        title=title,
        questions=questions,
        ordered_questions=ordered_questions,
        sections=sections,
        toc=toc,
        missing_explanations=missing_explanations,
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

    # Resolve every question to its node in the stored hierarchy: a question
    # read through a subtopic filter belongs to that subtopic, everything else
    # to its topic.  Paths come from the tree itself (not the possibly stale
    # ``category_path`` column), so renamed/moved categories print correctly.
    conn = get_connection(db_path)
    try:
        tree = get_category_tree(conn)
        node_paths = build_topic_paths(conn)
    finally:
        conn.close()
    section_order = assign_sections(questions, tree, node_paths)

    book_title = title or default_title
    raw_html = render_book_html(
        questions, title=book_title, include_answers=include_answers,
        partial=partial, partial_topics=partial_topics,
        section_order=section_order or None)
    final_html = _embed_local_images(raw_html, PROJECT_ROOT, DATA_DIR)

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
