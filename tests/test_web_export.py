"""
tests/test_web_export.py
Offline guarantees + category-tree export: the web bundle must never reference
external resources, must carry the hierarchy/progress data, bundled font, PWA
files, and must tolerate a half-finished database with --partial.
"""
import json
import re

import pytest

from medo.db import (
    get_category_progress,
    get_connection,
    init_db,
    refresh_question_paths,
    update_scrape_state,
    upsert_category,
    upsert_question,
)
from medo.exporter import WEB_DIR, export_web_bundle
from medo.models import AnswerSource, ParsedOption, ParsedQuestion
from medo.scraper import extract_menu_links


def _make_question(stem: str, url: str, ordinal: int) -> ParsedQuestion:
    q = ParsedQuestion(
        source_url=url,
        source_page=ordinal,
        ordinal=ordinal,
        stem_html=f"<p>{stem}</p>",
        stem_text=stem,
        options=[
            ParsedOption(index=i, text=f"گزینه {i}", raw_html=f"<span>{i}</span>")
            for i in range(1, 5)
        ],
        correct_index=2,
        answer_source=AnswerSource.MARKER,
    )
    q.compute_content_hash()
    return q


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "medo.db"
    conn = get_connection(db_path)
    init_db(conn)
    cat = upsert_category(
        conn, kind="category", title="طب داخلی",
        source_url="/qbank/cat-id-1", question_count=2,
    )
    upsert_category(
        conn, kind="topic", title="آریتمی‌ها",
        source_url="/qbank/topic-id-79", parent_id=cat, position=1,
        question_count=2,
    )
    upsert_question(
        conn, _make_question("بیمار ۶۰ ساله با تاکی‌کاردی", "/qbank/topic-id-79?page=1", 1),
        topic_id=79,
    )
    upsert_question(
        conn, _make_question("یافته‌های ECG در آریتمی دهلیزی", "/qbank/topic-id-79?page=2", 2),
        topic_id=79,
    )
    # Scraped but not finished -> partial build marker
    update_scrape_state(conn, 79, last_page=2, total_pages=5, status="running")
    conn.close()
    yield db_path


# --- offline guarantees ------------------------------------------------------

def test_index_html_has_no_external_resources():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert "fonts.googleapis.com" not in html
    assert "fonts.gstatic.com" not in html
    assert not re.search(r'(?:src|href)="https?://', html), "index.html must be fully offline"


def test_style_css_has_no_external_urls():
    css = (WEB_DIR / "style.css").read_text(encoding="utf-8")
    assert "fonts.googleapis" not in css
    assert not re.search(r"url\(\s*['\"]?https?://", css)
    assert "@font-face" in css
    assert "fonts/Vazirmatn-Regular.woff2" in css


def test_pwa_files_present():
    font = WEB_DIR / "fonts" / "Vazirmatn-Regular.woff2"
    assert font.is_file()
    assert font.read_bytes()[:4] == b"wOF2"
    assert (WEB_DIR / "manifest.json").is_file()
    assert (WEB_DIR / "sw.js").is_file()
    assert (WEB_DIR / "icons" / "icon-192.png").is_file()
    assert (WEB_DIR / "icons" / "icon-512.png").is_file()


# --- tree export -------------------------------------------------------------

def test_export_bundle_carries_tree_and_progress(seeded_db, tmp_path):
    out = export_web_bundle(output_dir=tmp_path / "dist", db_path=seeded_db, partial=True)

    cats_js = (out / "categories.js").read_text(encoding="utf-8")
    assert "window.MEDO_TREE" in cats_js
    assert "window.MEDO_PARTIAL = true" in cats_js
    assert "آریتمی‌ها" in cats_js

    tree = json.loads((out / "categories.json").read_text(encoding="utf-8"))
    assert tree[0]["title"] == "طب داخلی"
    topic = tree[0]["children"][0]
    assert topic["kind"] == "topic"
    assert topic["progress"]["scraped"] == 2
    assert topic["progress"]["declared"] == 2
    assert topic["progress"]["status"] == "running"
    assert topic["partial"] is True


def test_export_without_partial_is_not_flagged(seeded_db, tmp_path):
    out = export_web_bundle(output_dir=tmp_path / "dist2", db_path=seeded_db, partial=False)
    cats_js = (out / "categories.js").read_text(encoding="utf-8")
    assert "window.MEDO_PARTIAL = false" in cats_js
    tree = json.loads((out / "categories.json").read_text(encoding="utf-8"))
    assert tree[0]["children"][0]["partial"] is False


def test_export_copies_pwa_assets_and_injects_data_scripts(seeded_db, tmp_path):
    out = export_web_bundle(output_dir=tmp_path / "dist3", db_path=seeded_db)
    assert (out / "fonts" / "Vazirmatn-Regular.woff2").is_file()
    assert (out / "manifest.json").is_file()
    assert (out / "sw.js").is_file()
    assert (out / "icons" / "icon-192.png").is_file()
    assert (out / "questions.js").is_file()
    html = (out / "index.html").read_text(encoding="utf-8")
    assert '<script src="questions.js"></script>' in html
    assert '<script src="categories.js"></script>' in html
    assert "fonts.googleapis" not in html


# --- db helpers ---------------------------------------------------------------

def test_refresh_question_paths(seeded_db):
    conn = get_connection(seeded_db)
    updated = refresh_question_paths(conn)
    rows = conn.execute(
        "SELECT category_path FROM questions WHERE topic_id = 79"
    ).fetchall()
    conn.close()
    assert updated >= 2
    assert all("طب داخلی » آریتمی‌ها" in (r[0] or "") for r in rows)


def test_get_category_progress(seeded_db):
    conn = get_connection(seeded_db)
    prog = get_category_progress(conn)
    conn.close()
    topic = prog["/qbank/topic-id-79"]
    assert topic["scraped"] == 2
    assert topic["declared"] == 2
    assert topic["status"] == "running"


# --- scraper helper -----------------------------------------------------------

def test_extract_menu_links():
    html = """
    <a href="/qbank/cat-id-3">طب داخلی (120)</a>
    <a href="/qbank/topic-id-79">آریتمی‌ها ۴۵</a>
    <a href="/qbank/topic-id-79?page=2">آریتمی‌ها ۴۵</a>
    <a href="/about">skip me</a>
    """
    links = extract_menu_links(html)
    assert [l["site_id"] for l in links] == [3, 79]
    assert links[0]["kind"] == "cat"
    assert links[0]["title"] == "طب داخلی"
    assert links[0]["count"] == 120
    assert links[1]["kind"] == "topic"
    # normalize_single_line strips ZWNJ/zero-width chars by design
    assert links[1]["title"] == "آریتمیها"
    assert links[1]["count"] == 45
