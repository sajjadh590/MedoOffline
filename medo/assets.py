"""
medo/assets.py
Media asset downloader, SHA-1 deduplicator, and WebP optimizer.
"""
from __future__ import annotations

import hashlib
import io
import mimetypes
from pathlib import Path
from typing import Optional
import httpx
from PIL import Image

DEFAULT_ASSETS_DIR = Path("data/assets")


def compute_sha1(data: bytes) -> str:
    """Compute hex SHA-1 digest of binary data."""
    return hashlib.sha1(data).hexdigest()


def optimize_image(
    image_bytes: bytes,
    role: str = "question",
    max_width: int = 1600,
) -> tuple[bytes, str, str]:
    """
    Process image with Pillow:
    - Mindmaps or large images: convert to WebP, max width 1600px maintaining aspect ratio.
    - Other images: preserve format or convert to WebP if JPEG/PNG.
    Returns: (output_bytes, file_extension, mime_type)
    """
    try:
        im = Image.open(io.BytesIO(image_bytes))
        # Convert RGBA / P to RGB if saving as WebP or JPEG
        if im.mode in ("RGBA", "LA") and role == "mindmap":
            # WebP supports RGBA
            pass
        elif im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGB")

        # Resize if width exceeds max_width
        w, h = im.size
        if w > max_width:
            new_h = int(h * (max_width / w))
            im = im.resize((max_width, new_h), Image.Resampling.LANCZOS)

        out_buf = io.BytesIO()
        im.save(out_buf, format="WEBP", quality=85, method=6)
        return out_buf.getvalue(), ".webp", "image/webp"

    except Exception:
        # Fallback if image cannot be parsed by Pillow
        return image_bytes, ".png", "image/png"


def download_asset(
    url: str,
    assets_dir: Path | str = DEFAULT_ASSETS_DIR,
    role: str = "question",
    client: Optional[httpx.Client] = None,
) -> tuple[str, str, int, str]:
    """
    Download an asset, deduplicate by content SHA-1, optimize, and save to disk.
    Returns: (sha1, local_relative_path, bytes_size, mime_type)
    """
    dest_dir = Path(assets_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    close_client = False
    if client is None:
        client = httpx.Client(timeout=30.0, follow_redirects=True)
        close_client = True

    try:
        resp = client.get(url)
        resp.raise_for_status()
        raw_bytes = resp.content
    finally:
        if close_client:
            client.close()

    raw_sha1 = compute_sha1(raw_bytes)

    # Optimize mindmaps and diagrams
    if role in ("mindmap", "explanation") or len(raw_bytes) > 100_000:
        data, ext, mime = optimize_image(raw_bytes, role=role)
        final_sha1 = compute_sha1(data)
    else:
        # Infer extension from URL or mime
        guessed_ext = mimetypes.guess_extension(resp.headers.get("content-type", "")) or ".png"
        data = raw_bytes
        ext = guessed_ext
        mime = resp.headers.get("content-type", "image/png")
        final_sha1 = raw_sha1

    filename = f"{final_sha1}{ext}"
    local_path = dest_dir / filename

    if not local_path.is_file():
        local_path.write_bytes(data)

    return final_sha1, str(local_path.as_posix()), len(data), mime
