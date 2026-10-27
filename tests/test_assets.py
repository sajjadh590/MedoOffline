"""
tests/test_assets.py
Unit tests for medo/assets.py: Pillow WebP optimization and SHA-1 hashing.
"""
import io

import pytest
from PIL import Image

from medo.assets import compute_sha1, optimize_image
from medo.db import get_connection, init_db
from medo.models import ParsedImage, ParsedOption, ParsedQuestion
from medo.scraper import Harvester


def test_compute_sha1():
    data = b"medofast offline mirror"
    sha1 = compute_sha1(data)
    assert len(sha1) == 40
    assert isinstance(sha1, str)


def test_optimize_image_webp_conversion():
    # Create a dummy RGB image
    img = Image.new("RGB", (2000, 1000), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    out_bytes, ext, mime = optimize_image(png_bytes, role="mindmap", max_width=1600)
    assert ext == ".webp"
    assert mime == "image/webp"

    # Verify resized dimensions
    out_img = Image.open(io.BytesIO(out_bytes))
    assert out_img.size[0] <= 1600
    assert out_img.format == "WEBP"


def test_store_assets_points_the_markup_at_the_local_copy(monkeypatch, tmp_path):
    """Scraped images are downloaded *and* the stored HTML is rewritten.

    Without the rewrite the offline archive kept the site's absolute URLs and
    rendered broken images once the site was unreachable.
    """
    import medo.scraper as scraper_mod

    conn = get_connection(tmp_path / "medo.db")
    init_db(conn)

    url = "https://s3-strg.medofast.ir/q/fig-1.png"
    q = ParsedQuestion(
        source_url="https://medofast.ir/qbank/topic-id-83",
        source_page=1,
        ordinal=1,
        stem_html=f'<p>سوال</p><p><img src="{url}" alt="شکل"></p>',
        stem_text="سوال",
        options=[ParsedOption(index=1, text="الف", raw_html="الف")],
        correct_index=1,
        explanation_html=f'<p>توضیح <img src="{url}"></p>',
        explanation_text="توضیح",
        images=[ParsedImage(original_url=url, role="question")],
    )
    q.compute_content_hash()

    def fake_download(original_url, assets_dir=None, role="question"):
        path = tmp_path / "assets" / "deadbeef.webp"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"img")
        return "deadbeef", str(path), 3, "image/webp"

    monkeypatch.setattr(scraper_mod, "download_asset", fake_download)

    Harvester(db_path=tmp_path / "medo.db", assets_dir=tmp_path / "assets")._store_assets(conn, q)
    conn.close()

    assert 'src="assets/deadbeef.webp"' in q.stem_html
    assert url not in q.stem_html
    assert 'src="assets/deadbeef.webp"' in q.explanation_html
