"""
medo/exporter.py
Export database questions and web interface to standalone offline directory or single file.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Optional

from medo.db import (
    DEFAULT_DB_PATH,
    get_all_questions,
    get_category_progress,
    get_category_tree,
    get_connection,
    get_questions_by_topic,
)
from medo.paths import DEFAULT_ASSETS_DIR, DIST_DIR

WEB_DIR = Path(__file__).parent / "web"
DEFAULT_DIST_DIR = DIST_DIR


def export_web_bundle(
    output_dir: Path | str = DEFAULT_DIST_DIR,
    db_path: Path | str = DEFAULT_DB_PATH,
    topic_id: Optional[int] = None,
    assets_dir: Path | str = DEFAULT_ASSETS_DIR,
    partial: bool = False,
) -> Path:
    """
    Export the web viewer and database questions into a self-contained directory
    that runs cleanly offline in any browser without needing a server.

    partial=True marks topics whose scrape is still running/paused so the UI
    can badge them "ناقص" — but never fails on a half-finished database.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Fetch questions
    conn = get_connection(db_path)
    if topic_id is not None:
        questions = get_questions_by_topic(conn, topic_id)
    else:
        questions = get_all_questions(conn)
    conn.close()

    # 2. Write questions.json AND questions.js (for file:// protocol compatibility)
    q_json = json.dumps(questions, ensure_ascii=False, indent=2)
    q_file = out_dir / "questions.json"
    q_file.write_text(q_json, encoding="utf-8")

    q_js_file = out_dir / "questions.js"
    q_js_file.write_text(f"window.MEDO_QUESTIONS = {q_json};\n", encoding="utf-8")

    # 2b. Category hierarchy + per-node progress (drives tree, breadcrumbs)
    conn2 = get_connection(db_path)
    tree = get_category_tree(conn2)
    progress = get_category_progress(conn2)
    conn2.close()

    def _decorate(nodes: list[dict]) -> list[dict]:
        for n in nodes:
            n["progress"] = progress.get(n["source_url"], {})
            n["partial"] = bool(partial and (n["progress"].get("status") not in {"completed"}))
            _decorate(n.get("children", []))
        return nodes

    tree_json = json.dumps(_decorate(tree), ensure_ascii=False)
    (out_dir / "categories.js").write_text(
        f"window.MEDO_TREE = {tree_json};\nwindow.MEDO_PARTIAL = {json.dumps(bool(partial))};\n",
        encoding="utf-8",
    )
    (out_dir / "categories.json").write_text(tree_json, encoding="utf-8")

    # 3. Copy web assets (HTML, CSS, JS, PWA files)
    for fname in ("style.css", "app.js", "manifest.json", "sw.js"):
        src = WEB_DIR / fname
        if src.is_file():
            shutil.copy2(src, out_dir / fname)

    # Bundled font + icons (keeps the export 100% offline — no CDN)
    for dirname in ("fonts", "icons", "vendor"):
        src_dir = WEB_DIR / dirname
        if src_dir.is_dir():
            dest_dir = out_dir / dirname
            dest_dir.mkdir(parents=True, exist_ok=True)
            for item in src_dir.iterdir():
                if item.is_file():
                    shutil.copy2(item, dest_dir / item.name)

    # Copy and adapt index.html to load data before app.js
    html_src = WEB_DIR / "index.html"
    if html_src.is_file():
        html_content = html_src.read_text(encoding="utf-8")
        if '<script src="questions.js"></script>' not in html_content:
            html_content = html_content.replace(
                '<script src="app.js"></script>',
                '<script src="questions.js"></script>\n  <script src="categories.js"></script>\n  <script src="app.js"></script>',
            )
        (out_dir / "index.html").write_text(html_content, encoding="utf-8")

    # 4. Copy image assets if directory exists
    src_assets = Path(assets_dir)
    if src_assets.is_dir():
        dest_assets = out_dir / "assets"
        dest_assets.mkdir(parents=True, exist_ok=True)
        for item in src_assets.glob("*"):
            if item.is_file():
                shutil.copy2(item, dest_assets / item.name)

    return out_dir
