"""Live probe of the real medofast tree: category → lesson (/lobey/N) → topic.

Uses curl with --resolve to bypass the machine's fake-IP DNS (198.18.x.x) that
resets TLS to medofast.ir. Prints one line per node; saves pages under
logs/probe/ for offline inspection.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from medo.netfix import resolve_public_ip  # noqa: E402

# Real address when the VPN's fake-IP DNS would reset the TLS handshake.
IP = os.environ.get("MEDO_IP") or resolve_public_ip() or "185.143.233.132"
BASE = "https://medofast.ir"
OUT = Path("logs/probe")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

TOPIC_RE = re.compile(r"/qbank/topic-id-(\d+)")
CAT_RE = re.compile(r"/qbank/cat-id-(\d+)")


def fetch(path: str) -> str:
    name = path.strip("/").replace("/", "_") or "root"
    dest = OUT / f"{name}.html"
    subprocess.run(
        ["curl", "-s", "-o", str(dest), "--resolve", f"medofast.ir:443:{IP}",
         "--max-time", "40", "-H", f"User-Agent: {UA}",
         "-H", "Accept: text/html,application/xhtml+xml",
         "-H", "Accept-Language: fa-IR,fa;q=0.9,en;q=0.8",
         "-H", f"Referer: {BASE}/qbank/", BASE + path],
        check=False, timeout=60,
    )
    return dest.read_text(encoding="utf-8", errors="replace")


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def lesson_cards(html: str) -> list[dict]:
    out: list[dict] = []
    for m in re.finditer(r'<a\s+href="(/lobey/(\d+))"[^>]*>(.*?)</a>', html, re.S):
        block = m.group(3)
        h2 = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        tid = re.search(r'data-topic-id="(\d+)"', block)
        out.append({
            "href": m.group(1),
            "lesson_id": int(m.group(2)),
            "title": clean(h2.group(1)) if h2 else "",
            "topic_id": int(tid.group(1)) if tid else None,
        })
    return out


def topic_rows(html: str) -> list[dict]:
    """Rows of the 'حالت دوم : مطالعه مبحثی' table: title + count + topic id."""
    out: list[dict] = []
    for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        row = m.group(1)
        tm = TOPIC_RE.search(row)
        if not tm:
            continue
        cells = [clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        title = cells[0] if cells else ""
        count = 0
        if len(cells) > 1:
            digits = re.search(r"[\d۰-۹]+", cells[1])
            if digits:
                count = int(digits.group(0).translate(
                    str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")))
        out.append({"topic_id": int(tm.group(1)), "title": title, "count": count})
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cats: dict[int, list[dict]] = {}
    for entry in ("/qbank", "/qbank-keshvari", "/qbank-talifi"):
        html = fetch(entry)
        ids = sorted({int(m) for m in CAT_RE.findall(html)})
        print(f"[entry] {entry}: cats {ids}")
        for cid in ids:
            if cid in cats:
                continue
            chtml = fetch(f"/qbank/cat-id-{cid}")
            lessons = lesson_cards(chtml)
            cats[cid] = lessons
            print(f"[cat {cid}] {len(lessons)} lesson(s)")
            for l in lessons:
                print(f"   lesson {l['lesson_id']:>4} topic={l['topic_id']}  {l['title']}")

    total = 0
    for _cid, lessons in cats.items():
        for l in lessons:
            html = fetch(l["href"])
            rows = topic_rows(html)
            uniq = {}
            for r in rows:
                uniq.setdefault(r["topic_id"], r)
            total += len(uniq)
            sample = [(r["title"], r["count"]) for r in list(uniq.values())[:3]]
            print(f"[lesson {l['lesson_id']}] {l['title']}: {len(uniq)} topic(s) {sample}")
    print(f"TOTAL topics: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
