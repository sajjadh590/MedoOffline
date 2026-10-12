"""
tests/test_assets.py
Unit tests for medo/assets.py: Pillow WebP optimization and SHA-1 hashing.
"""
import io
from PIL import Image
from medo.assets import compute_sha1, optimize_image


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
