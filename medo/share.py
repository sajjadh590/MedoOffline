"""
medo/share.py
Hand the archive to the Android app — offline, no accounts, no cloud.

Two routes, same payload:

* ``write_bundle()`` produces a ``.medo`` file — a ZIP holding ``index.json``
  (tree + one light entry per question) and one JSON chunk per top-level
  category — that the phone imports through the system file picker (copy with
  a USB cable, or ``adb push``).  The old single-JSON file is still read by
  the app, so an already-installed bank keeps working.
* ``start_share()`` runs a tiny **read-only** HTTP server on the LAN so the
  app's «دریافت از وای‌فای» imports the same payload; over USB,
  ``adb reverse tcp:8765 tcp:8765`` makes it reachable at 127.0.0.1.

The practice server (medo/server.py) is deliberately not reused: it exposes
state-changing routes and refuses non-loopback Host headers.  This one only
answers GET for three paths and never writes anything.
"""
from __future__ import annotations

import base64
import io
import json
import re
import socket
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from medo.db import (
    DEFAULT_DB_PATH,
    get_all_questions,
    get_category_progress,
    get_category_tree,
    get_connection,
)
from medo.paths import DEFAULT_ASSETS_DIR, DIST_DIR

FORMAT_NAME = "medo-bank"
# v2 = zip (index + per-category chunks); v1 = one JSON document.
FORMAT_VERSION = 2
INDEX_NAME = "index.json"
CHUNK_DIR = "chunks"
BUNDLE_FILENAME = "medo.medo"
DEFAULT_SHARE_PORT = 8765

# Only these fields travel to the phone: the database keeps scrape bookkeeping
# (content_hash, download_bytes, ...) that the app has no use for, and a smaller
# bundle is a faster Wi-Fi import on a slow link.
_QUESTION_FIELDS = (
    "id", "topic_id", "tag_id", "nodes", "source_url", "page", "ordinal",
    "stem_html", "stem_text", "options", "correct", "answer_source", "votes",
    "explanation_html", "explanation_text", "explanation_status",
    "has_images", "path", "bookmarked", "selected",
)
_TREE_FIELDS = (
    "id", "parent_id", "kind", "title", "source_url", "position",
    "question_count", "progress", "partial", "children",
)


def _clean_question(row: dict) -> dict:
    """One database row -> the compact dict the app parses."""
    options = []
    for opt in row.get("options") or []:
        try:
            options.append({"index": int(opt["index"]), "text": str(opt.get("text") or "")})
        except (KeyError, TypeError, ValueError):
            continue
    # Every node the question was reached through ({topic_id, tag_id}); the
    # single topic_id/tag_id pair below stays for older phone builds, which
    # read it as the primary node.
    nodes = [{"topic_id": int(n["topic_id"]), "tag_id": int(n.get("tag_id") or 0)}
             for n in (row.get("nodes") or []) if n.get("topic_id")]
    return {
        "id": int(row["id"]),
        "topic_id": row.get("topic_id"),
        "tag_id": row.get("tag_id"),
        "nodes": nodes,
        "source_url": row.get("source_url") or "",
        "page": row.get("source_page") or 0,
        "ordinal": row.get("ordinal") or 0,
        "stem_html": row.get("stem_html") or "",
        "stem_text": row.get("stem_text") or "",
        "options": options,
        "correct": row.get("correct_index"),
        "answer_source": row.get("answer_source") or "unknown",
        "votes": row.get("vote_percent") or {},
        "explanation_html": row.get("explanation_html") or "",
        "explanation_text": row.get("explanation_text") or "",
        "explanation_status": row.get("explanation_status") or "unknown",
        "has_images": bool(row.get("has_images")),
        "path": row.get("category_path") or "",
        # Progress already recorded in the web UI travels with the bank, so the
        # phone does not start from zero if the two are used side by side.
        "bookmarked": bool(row.get("bookmarked")),
        "selected": row.get("user_selected_option"),
    }


def _clean_node(node: dict) -> dict:
    out = {
        "id": node.get("id"),
        "parent_id": node.get("parent_id"),
        "kind": node.get("kind") or "topic",
        "title": node.get("title") or "",
        "source_url": node.get("source_url") or "",
        "position": node.get("position") or 0,
        "question_count": node.get("question_count") or 0,
        "progress": node.get("progress") or {},
        "partial": bool(node.get("partial")),
        "children": [_clean_node(c) for c in node.get("children", [])],
    }
    return out


def _decorate(nodes: list[dict], progress: dict) -> list[dict]:
    for n in nodes:
        n["progress"] = progress.get(n["source_url"], {})
        n["partial"] = bool(
            n["progress"].get("status") not in {"completed"}
            and n["progress"].get("scraped", 0) > 0
        )
        _decorate(n.get("children", []), progress)
    return nodes


def _topic_id(source_url: str) -> int:
    """\"/qbank/topic-id-79?tag=3\" -> 79 (0 for a non-topic node)."""
    m = re.search(r"topic-id-(\d+)", source_url or "")
    return int(m.group(1)) if m else 0


def _topic_roots(tree: list[dict]) -> dict[int, int]:
    """topic id -> id of the top-level category it is filed under."""
    roots: dict[int, int] = {}

    def walk(nodes: list[dict], root_id: int) -> None:
        for n in nodes:
            rid = root_id or int(n.get("id") or 0)
            tid = _topic_id(n.get("source_url") or "")
            if tid:
                roots.setdefault(tid, rid)
            walk(n.get("children") or [], rid)

    walk(tree, 0)
    return roots


def _titles_by_root(tree: list[dict]) -> dict[int, str]:
    return {int(n.get("id") or 0): str(n.get("title") or "") for n in tree}


def _selected_topics(tree: list[dict], wanted: set[int]) -> set[int]:
    """Resolve category ids, category node ids and topic ids to topic ids."""
    chosen: set[int] = set()

    def walk(nodes: list[dict], inside: bool) -> None:
        for n in nodes:
            tid = _topic_id(n.get("source_url") or "")
            hit = inside or int(n.get("id") or 0) in wanted or tid in wanted
            if tid and hit:
                chosen.add(tid)
            walk(n.get("children") or [], hit)

    walk(tree, False)
    return chosen


def _prune_tree(nodes: list[dict], topics: set[int]) -> list[dict]:
    """The tree with the branches that hold none of `topics` removed."""
    kept: list[dict] = []
    for n in nodes:
        children = _prune_tree(n.get("children") or [], topics)
        tid = _topic_id(n.get("source_url") or "")
        if children or (tid and tid in topics):
            clone = dict(n)
            clone["children"] = children
            kept.append(clone)
    return kept


def _inline_images(html: str, assets_dir: Path, max_width: int = 900) -> str:
    """Turn ``src="assets/x.webp"`` into a small WebP data URI for the phone.

    The phone has no assets folder next to its bank file, so an image that the
    web archive shows inline would render broken there.  Opt-in, because a
    mind-map per lesson is most of the bundle's size.
    """
    from medo.assets import optimize_image

    def repl(match: re.Match) -> str:
        path = assets_dir / match.group(1)
        if not path.is_file():
            return match.group(0)
        try:
            data, _ext, mime = optimize_image(path.read_bytes(), role="mindmap",
                                               max_width=max_width)
        except Exception:
            return match.group(0)
        return 'src="data:%s;base64,%s"' % (
            mime, base64.b64encode(data).decode("ascii"))

    return re.sub(r'src="assets/([^"]+)"', repl, html or "")


def build_android_bundle(
    db_path: Path | str = DEFAULT_DB_PATH,
    categories: Optional[list[int]] = None,
    include_images: bool = False,
    assets_dir: Path | str = DEFAULT_ASSETS_DIR,
) -> dict:
    """The archive as one JSON-able dict (stable key order, no NaN).

    ``categories`` keeps only the topics under the given category/topic ids;
    ``include_images`` embeds small WebP mind-maps into the lesson HTML.
    """
    conn = get_connection(db_path)
    try:
        rows = get_all_questions(conn)
        tree = _decorate(get_category_tree(conn), get_category_progress(conn))
    finally:
        conn.close()

    tree = [_clean_node(n) for n in tree]
    if categories:
        wanted = {int(c) for c in categories if int(c) > 0}
        topics = _selected_topics(tree, wanted)
        rows = [r for r in rows
                if any(int(n.get("topic_id") or 0) in topics
                       for n in (r.get("nodes") or []))]
        tree = _prune_tree(tree, topics)

    questions = [_clean_question(r) for r in rows]
    if include_images:
        assets = Path(assets_dir)
        for q in questions:
            q["stem_html"] = _inline_images(q["stem_html"], assets)
            q["explanation_html"] = _inline_images(q["explanation_html"], assets)

    answered = sum(1 for q in questions if q["correct"] not in (None, 0))
    stats = {
        "total": len(questions),
        "with_answer": answered,
        # Explicit, because the app must not call these questions «wrong»:
        # the site did not reveal an answer when they were harvested.
        "missing_answer": len(questions) - answered,
        "with_explanation": sum(1 for q in questions if q["explanation_text"]),
        "bookmarked": sum(1 for q in questions if q["bookmarked"]),
        "with_images": include_images,
    }
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "stats": stats,
        "tree": tree,
        "questions": questions,
    }


# What the index keeps per question: enough to show a list, count the tree and
# search, without the lesson HTML that makes a bundle heavy.
_INDEX_FIELDS = (
    "id", "topic_id", "tag_id", "nodes", "page", "ordinal", "stem_text",
    "correct", "answer_source", "explanation_status", "has_images", "path",
    "bookmarked", "selected",
)


def index_and_chunks(payload: dict) -> tuple[dict, dict[int, list[dict]]]:
    """Split a full bundle into the zip's index.json and its chunk arrays."""
    roots = _topic_roots(payload.get("tree") or [])
    titles = _titles_by_root(payload.get("tree") or [])
    chunks: dict[int, list[dict]] = {}
    light: list[dict] = []
    for q in payload.get("questions") or []:
        cid = roots.get(int(q.get("topic_id") or 0), 0)
        chunks.setdefault(cid, []).append(q)
        entry = {k: q.get(k) for k in _INDEX_FIELDS}
        entry["chunk"] = cid
        light.append(entry)

    index = {k: payload.get(k) for k in
             ("format", "version", "generated_at", "stats", "tree")}
    index["questions"] = light
    index["chunks"] = [
        {"id": cid, "title": titles.get(cid, ""), "count": len(qs),
         "file": f"{CHUNK_DIR}/{cid}.json"}
        for cid, qs in chunks.items()
    ]
    return index, chunks


def bundle_bytes(
    db_path: Path | str = DEFAULT_DB_PATH,
    categories: Optional[list[int]] = None,
    include_images: bool = False,
) -> bytes:
    """The exact bytes the app downloads: a zip of index + category chunks."""
    payload = build_android_bundle(db_path, categories=categories,
                                   include_images=include_images)
    index, chunks = index_and_chunks(payload)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(INDEX_NAME, json.dumps(index, ensure_ascii=False,
                                           separators=(",", ":")))
        for cid, questions in chunks.items():
            body = {"format": FORMAT_NAME, "version": FORMAT_VERSION,
                    "chunk": cid, "questions": questions}
            zf.writestr(f"{CHUNK_DIR}/{cid}.json",
                        json.dumps(body, ensure_ascii=False,
                                   separators=(",", ":")))
    return buf.getvalue()


def read_bundle(blob: bytes) -> dict:
    """Undo :func:`bundle_bytes` (tests, inspection, a v1 JSON file)."""
    if blob[:2] != b"PK":
        return json.loads(blob.decode("utf-8"))
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        index = json.loads(zf.read(INDEX_NAME).decode("utf-8"))
        questions: list[dict] = []
        for chunk in index.get("chunks") or []:
            part = json.loads(zf.read(chunk["file"]).decode("utf-8"))
            questions.extend(part.get("questions") or [])
    merged = dict(index)
    merged["questions"] = questions
    return merged


def write_bundle(
    out_path: Path | str = DIST_DIR / BUNDLE_FILENAME,
    db_path: Path | str = DEFAULT_DB_PATH,
    categories: Optional[list[int]] = None,
    include_images: bool = False,
) -> Path:
    """Write ``out_path`` (.medo) atomically; returns the file path."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    blob = bundle_bytes(db_path, categories=categories,
                        include_images=include_images)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(out)
    return out


def _rank(address: str) -> int:
    """Lower sorts better: the phone has to be able to reach this address.

    Docker/WSL bridges (172.16/12), VPN or CGNAT pools (100.64/10) and
    link-local addresses look like LAN addresses to the routing table but are
    unreachable from a phone — the QR used to recommend 172.18.0.1 that way.
    """
    parts = address.split(".")
    if len(parts) != 4:
        return 3
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError:
        return 3
    if first == 192 and second == 168:
        return 0
    if first == 10:
        return 1
    if (first == 172 and 16 <= second <= 31) or first == 100 \
            or address.startswith("169.254."):
        return 3
    return 2


def lan_addresses() -> list[str]:
    """IPv4 addresses this machine can be reached at from the phone."""
    found: list[str] = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))   # no packet is sent for UDP
            found.append(sock.getsockname()[0])
        finally:
            sock.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addr = info[4][0]
            if addr not in found and not addr.startswith("127."):
                found.append(addr)
    except OSError:
        pass
    found.sort(key=_rank)
    return found


_INDEX_HTML = """<!doctype html>
<html lang="fa" dir="rtl"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>مدو — نصب و انتقال بانک</title>
<style>
 body{{margin:0;background:#0e1116;color:#e6edf3;font-family:Tahoma,sans-serif;
      display:flex;min-height:100vh;justify-content:center}}
 .wrap{{max-width:520px;width:100%;margin:0;padding:22px 18px 32px}}
 h1{{font-size:21px;margin:0 0 4px}} .sub{{color:#7d8b99;font-size:12px;margin:0 0 20px}}
 .step{{background:#161b22;border:1px solid #26303a;border-radius:16px;padding:16px;margin:12px 0}}
 .num{{display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;
      border-radius:50%;background:#0c2a24;color:#2dd4a7;font-size:12px;margin-left:6px}}
 .t{{font-size:15px;font-weight:bold}} p{{color:#9aa7b4;line-height:1.85;font-size:13px;margin:8px 0 0}}
 a.btn{{display:block;text-align:center;margin-top:12px;padding:14px;border-radius:12px;
        background:#2dd4a7;color:#08120e;font-weight:bold;text-decoration:none;font-size:15px}}
 a.btn.alt{{background:#1c242d;color:#e6edf3;border:1px solid #27313b}}
 code{{background:#0e1116;padding:2px 6px;border-radius:6px;direction:ltr;display:inline-block;
      font-size:12px;color:#2dd4a7}}
 .n{{color:#5d6b78;font-size:11px;margin-top:10px}}
</style>
<div class="wrap">
  <h1>مدو آفلاین</h1>
  <p class="sub">{count} سوال · {size} · گوشی و کامپیوتر روی یک شبکه</p>

  <div class="step">
    <div class="t"><span class="num">۱</span>نصب برنامه</div>
    <p>اگر برنامه نصب نیست، فایل APK را بگیر و باز کن؛ اندروید اجازهٔ نصب از مرورگر را می‌پرسد.</p>
    <a class="btn" href="/medo.apk">دانلود medo.apk ({apk_size})</a>
  </div>

  <div class="step">
    <div class="t"><span class="num">۲</span>دریافت بانک سوال</div>
    <p>فایل بانک را دانلود کن، بعد در برنامه: <b>دریافت بانک ← از فایل</b> و
       از پوشهٔ Downloads همین فایل را انتخاب کن.</p>
    <a class="btn alt" href="/{name}">دانلود {name} ({size})</a>
    <p>یا در برنامه: <b>دریافت از وای‌فای</b> با آدرس <code>{host}</code></p>
    <p class="n">USB: <code>adb reverse tcp:{port} tcp:{port}</code> و آدرس
       <code>127.0.0.1:{port}</code></p>
  </div>
</div>
</html>
"""


def _human_size(n: int) -> str:
    for unit in ("بایت", "کیلوبایت", "مگابایت"):
        if n < 1024 or unit == "مگابایت":
            return f"{n:.0f} {unit}" if unit == "بایت" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n} بایت"


def _make_handler(
    blob: bytes,
    db_path: Path | str,
    apk_path: Optional[Path] = None,
) -> type[BaseHTTPRequestHandler]:
    count = 0
    try:
        if blob[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                count = json.loads(zf.read(INDEX_NAME))["stats"]["total"]
        else:
            count = json.loads(blob.decode("utf-8"))["stats"]["total"]
    except (KeyError, ValueError, UnicodeDecodeError, zipfile.BadZipFile):
        pass
    apk_file = Path(apk_path) if apk_path else None

    class _Handler(BaseHTTPRequestHandler):
        server_version = "MedoShare/1.0"
        protocol_version = "HTTP/1.1"

        def _send(self, code: int, body: bytes, ctype: str, extra=None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_HEAD(self):      # noqa: N802 — BaseHTTPRequestHandler naming
            self.do_GET()

        def do_GET(self):       # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                host = self.headers.get("Host") or "127.0.0.1"
                html = _INDEX_HTML.format(
                    name=BUNDLE_FILENAME, size=_human_size(len(blob)),
                    count=count, port=self.server.server_address[1],
                    host=host,
                    apk_size=(_human_size(apk_file.stat().st_size)
                              if apk_file and apk_file.is_file() else "ناموجود"),
                ).encode("utf-8")
                self._send(200, html, "text/html; charset=utf-8")
            elif path in ("/medo.apk", "/app.apk"):
                if not (apk_file and apk_file.is_file()):
                    self._send(404, b'{"ok":false,"error":"build the APK first: bash android/build.sh"}',
                               "application/json")
                    return
                self._send(
                    200, apk_file.read_bytes(),
                    "application/vnd.android.package-archive",
                    {"Content-Disposition": 'attachment; filename="medo.apk"'},
                )
            elif path in (f"/{BUNDLE_FILENAME}", "/bank.json", "/medo.json"):
                self._send(
                    200, blob,
                    ("application/zip" if blob[:2] == b"PK"
                     else "application/json; charset=utf-8"),
                    {"Content-Disposition": f'attachment; filename="{BUNDLE_FILENAME}"'},
                )
            elif path == "/health":
                self._send(200, b'{"ok":true}', "application/json")
            else:
                self._send(404, b'{"ok":false}', "application/json")

        def log_message(self, fmt: str, *args) -> None:    # keep the console tidy
            try:
                from medo import applog
                applog.log("[share] " + (fmt % args))
            except Exception:
                pass

    return _Handler


def start_share(
    db_path: Path | str = DEFAULT_DB_PATH,
    port: int = DEFAULT_SHARE_PORT,
    bind: str = "0.0.0.0",
    apk_path: Optional[Path | str] = None,
) -> ThreadingHTTPServer:
    """Build the bundle once and serve it (plus the APK) until interrupted."""
    blob = bundle_bytes(db_path)
    if apk_path is None:
        candidate = DIST_DIR / "medo.apk"
        apk_path = candidate if candidate.is_file() else None
    handler = _make_handler(blob, db_path, apk_path)
    httpd = ThreadingHTTPServer((bind, int(port)), handler)
    httpd.daemon_threads = True       # Ctrl+C must not wait for an open phone
    return httpd


def share_urls(port: int) -> list[str]:
    """URLs to type into the app, LAN first, loopback (adb reverse) last."""
    urls = [f"http://{ip}:{port}" for ip in lan_addresses()]
    urls.append(f"http://127.0.0.1:{port}")
    return urls
