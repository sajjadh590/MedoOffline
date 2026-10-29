#!/usr/bin/env python
"""Refuse to push anything the repository is not allowed to hold.

The archive itself is private: session cookies, scraped pages and the SQLite
bank live under ``data/`` and are git-ignored.  A single ``git add -f`` or a
renamed export would put paid content on GitHub, where it cannot be taken back.

Run before every push (and in the clean-clone test)::

    python scripts/dev/audit_push.py

It lists every file git tracks and fails on: sessions/cookies, databases,
saved pages, ``data/``-like folders, files over 1 MB, a line of Persian prose
long enough to be lesson text, and text copied from ``data/diag/*.mhtml``.
Only paths and counts are printed — never the content itself.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

MAX_BYTES = 1_000_000
PROSE_LIMIT = 300            # Persian letters in one line; the UI never needs more
CAPTURE_WINDOW = 120         # normalised chars compared against a capture
CAPTURE_STEP = 60

FORBIDDEN_DIRS = {"data", "logs", "dist", "tools", "build", ".venv", "venv"}
FORBIDDEN_SUFFIXES = {
    ".db", ".db-wal", ".db-shm", ".mhtml", ".mht", ".medo", ".keystore",
    ".jks", ".log", ".sqlite", ".sqlite3",
}
FORBIDDEN_NAMES = ("storage_state", "cookies.json")

# Checked-in binaries are allowed only when they are small, needed offline and
# carry no private data.  Everything else (an APK, a keystore, a database) is
# a build artifact and must stay local.  Printed by every run so the list is
# not a secret rule.
BINARY_ALLOWLIST = {
    "android/libs/zxing-core.jar":
        "offline QR decoding dependency (607 KB, no Gradle/network)",
    "medo/web/fonts/Vazirmatn-Bold.woff2": "bundled Persian font (51 KB, offline UI)",
    "medo/web/fonts/Vazirmatn-Light.woff2": "bundled Persian font (51 KB, offline UI)",
    "medo/web/fonts/Vazirmatn-Medium.woff2": "bundled Persian font (51 KB, offline UI)",
    "medo/web/fonts/Vazirmatn-Regular.woff2": "bundled Persian font (51 KB, offline UI)",
    "medo/web/fonts/Vazirmatn-SemiBold.woff2": "bundled Persian font (51 KB, offline UI)",
    "medo/web/icons/icon-192.png": "PWA icon (8 KB)",
    "medo/web/icons/icon-512.png": "PWA icon (26 KB)",
    "medo/web/icons/medo.ico": "PWA icon (24 KB)",
}
TEXT_SUFFIXES = {
    ".py", ".js", ".java", ".md", ".txt", ".html", ".htm", ".css", ".json",
    ".xml", ".sh", ".bat", ".ps1", ".toml", ".cfg", ".ini", ".yml", ".yaml",
    ".csv", ".svg", ".mjs",
}

_FA = re.compile(r"[\u0600-\u06FF]")
_WS = re.compile(r"[\s\u200c\u200d\u200e\u200f\ufeff]+")


def tracked_files(root: Path = ROOT) -> list[str] | None:
    """Every path git tracks, or None when `root` is not its own repo.

    ``git archive`` exports have no ``.git``; running ``git ls-files`` from
    inside such a folder would list the parent repository instead, so the
    caller must walk the exported tree.
    """
    if not (root / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True, check=False)
    if result.returncode != 0:
        raise SystemExit("این پوشه مخزن git نیست یا git در دسترس نیست.")
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def all_files(root: Path = ROOT) -> list[str]:
    """Every file in an exported tree (no .git), relative to `root`."""
    found: list[str] = []
    for path in root.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            found.append(path.relative_to(root).as_posix())
    return sorted(found)


def _bare(text: str) -> str:
    """Text with all whitespace/half-space removed, for copy detection."""
    return _WS.sub("", text)


def _capture_texts(root: Path) -> list[tuple[str, str]]:
    """(file name, normalised text) for each private capture, if present."""
    folder = root / "data" / "diag"
    if not folder.is_dir():
        return []
    try:
        from medo.parser import extract_html_from_mhtml, parse_question_html
    except Exception:
        return []
    found: list[tuple[str, str]] = []
    for path in sorted(folder.glob("*.mhtml")) + sorted(folder.glob("*.mht")):
        try:
            html = extract_html_from_mhtml(path.read_bytes())
            parsed = parse_question_html(html, submitted=False)
        except Exception:
            continue
        parts = [parsed.stem_text or "", parsed.explanation_text or ""]
        parts += [o.text or "" for o in parsed.options]
        text = _bare("\n".join(parts))
        if len(text) >= CAPTURE_WINDOW:
            found.append((path.name, text))
    return found


def is_binary_allowlisted(rel: str) -> bool:
    """True for a checked-in binary whose size and purpose are documented."""
    return rel in BINARY_ALLOWLIST


def _read_text(path: Path) -> str | None:
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return None
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    return raw.decode("utf-8", "replace")


def audit_paths(root: Path, paths: list[str]) -> list[str]:
    """Findings as `path: reason` lines.  No file content is included."""
    findings: list[str] = []
    captures = _capture_texts(root)
    bare_cache: dict[str, str] = {}

    for rel in paths:
        path = root / rel
        lower = rel.lower()
        parts = {p.lower() for p in Path(rel).parts[:-1]}
        name = Path(rel).name.lower()

        if parts & FORBIDDEN_DIRS:
            findings.append(f"{rel}: under a private folder")
            continue
        if any(bad in name for bad in FORBIDDEN_NAMES):
            findings.append(f"{rel}: session/cookie file")
            continue
        if Path(rel).suffix.lower() in FORBIDDEN_SUFFIXES:
            findings.append(f"{rel}: private/generated file type")
            continue
        try:
            size = path.stat().st_size
        except OSError:
            findings.append(f"{rel}: tracked but missing")
            continue
        if size > MAX_BYTES:
            findings.append(f"{rel}: {size / 1_000_000:.1f} MB (limit 1 MB)")
            continue

        text = _read_text(path)
        if text is None:
            continue
        long_line = max((len(_FA.findall(line)) for line in text.splitlines()),
                        default=0)
        if long_line >= PROSE_LIMIT:
            findings.append(f"{rel}: {long_line} Persian letters on one line")
            continue

        if not captures:
            continue
        bare = bare_cache.get(rel)
        if bare is None:
            bare = bare_cache[rel] = _bare(text)
            if len(bare) < CAPTURE_WINDOW:
                continue
        for capture_name, capture in captures:
            hit = False
            for start in range(0, len(capture) - CAPTURE_WINDOW + 1, CAPTURE_STEP):
                if capture[start:start + CAPTURE_WINDOW] in bare:
                    hit = True
                    break
            if hit:
                findings.append(
                    f"{rel}: text copied from data/diag/{capture_name}")
                break
    return findings


def _git(root: Path, *args: str, binary: bool = False):
    result = subprocess.run(["git", "-C", str(root), *args],
                            capture_output=True, check=False)
    if binary:
        return result.stdout
    return result.stdout.decode("utf-8", "replace")


def _history_entries(root: Path) -> list[tuple[str, str]]:
    """(object id, path) for every blob reachable from any ref."""
    out = _git(root, "rev-list", "--objects", "--all")
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for line in out.splitlines():
        oid, _, rel = line.partition(" ")
        if not rel or not oid:
            continue
        key = (oid, rel)
        if key in seen:
            continue
        seen.add(key)
        found.append(key)
    return found


def _commit_for(root: Path, oid: str) -> str:
    out = _git(root, "log", "--all", "--format=%H", f"--find-object={oid}",
               "-1").strip()
    return out.splitlines()[0] if out else "?"


def _blob(root: Path, oid: str) -> bytes | None:
    data = _git(root, "cat-file", "-p", oid, binary=True)
    return data or None


def scan_history(root: Path = ROOT) -> list[str]:
    """Findings in every reachable commit, as `path @ commit: reason`.

    Read-only: history is never rewritten.  The current capture texts (if the
    owner still has data/diag) are used for copy detection; without them the
    rule honestly cannot fire, and the summary says so.
    """
    if not (root / ".git").exists():
        return ["history: not a git checkout"]
    captures = _capture_texts(root)
    findings: list[str] = []
    for oid, rel in _history_entries(root):
        lower = rel.lower()
        parts = {p.lower() for p in Path(rel).parts[:-1]}
        name = Path(rel).name.lower()
        reason = None
        if is_binary_allowlisted(rel):
            continue
        if parts & FORBIDDEN_DIRS:
            reason = "under a private folder"
        elif any(bad in name for bad in FORBIDDEN_NAMES):
            reason = "session/cookie file"
        elif Path(rel).suffix.lower() in FORBIDDEN_SUFFIXES:
            reason = "private/generated file type"
        else:
            data = _blob(root, oid)
            if data is None:
                reason = "unreadable blob"
            elif len(data) > MAX_BYTES:
                reason = f"{len(data) / 1_000_000:.1f} MB (limit 1 MB)"
            elif Path(rel).suffix.lower() in TEXT_SUFFIXES and b"\x00" not in data[:4096]:
                text = data.decode("utf-8", "replace")
                long_line = max((len(_FA.findall(line))
                                 for line in text.splitlines()), default=0)
                if long_line >= PROSE_LIMIT:
                    reason = f"{long_line} Persian letters on one line"
                elif captures:
                    bare = _bare(text)
                    for capture_name, capture in captures:
                        if len(bare) < CAPTURE_WINDOW:
                            continue
                        if any(capture[start:start + CAPTURE_WINDOW] in bare
                               for start in range(0, len(capture) - CAPTURE_WINDOW + 1,
                                                  CAPTURE_STEP)):
                            reason = f"text copied from data/diag/{capture_name}"
                            break
        if reason:
            commit = _commit_for(root, oid)
            findings.append(f"{rel} @ {commit[:12]}: {reason}")
    return findings


def _print_allowlist() -> None:
    for rel, why in BINARY_ALLOWLIST.items():
        print(f"[audit_push] allowlisted binary: {rel} — {why}")
    print("[audit_push] NOT allowlisted: any *.apk (e.g. dist/medo.apk), "
          "*.db, *.mhtml, keystore/log/session file — keep them local.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true",
                        help="scan every reachable commit, not just the index")
    args = parser.parse_args()
    _print_allowlist()
    if args.history:
        findings = scan_history(ROOT)
        for line in findings:
            print(f"FAIL {line}")
        print(f"[audit_push] history: {len(findings)} finding(s) across "
              f"{_git(ROOT, 'rev-list', '--all', '--count').strip()} commit(s)")
        if findings:
            print("[audit_push] history was NOT rewritten. To clean it you would "
                  "need git filter-repo/filter-branch — ask the owner first; "
                  "a force-push is destructive and is never done automatically.")
        return 1 if findings else 0

    try:
        paths = tracked_files(ROOT)
    except SystemExit as exc:
        print(f"[!] {exc}")
        return 2
    scope = "tracked"
    if paths is None:
        paths, scope = all_files(ROOT), "exported"
    findings = audit_paths(ROOT, paths)
    for line in findings:
        print(f"FAIL {line}")
    print(f"[audit_push] {len(paths)} {scope} file(s), {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
