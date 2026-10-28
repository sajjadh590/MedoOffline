#!/usr/bin/env python
"""Read-only report of what the parser extracts from the private captures.

Put the owner's saved pages in ``data/diag/*.mhtml`` (git-ignored) and run::

    python scripts/dev/check_captures.py

For every capture it prints the stem, the options with their site vote share,
the correct option, the explanation status and length, how many invisible code
points survived, whether a dropped decoy block leaked into the lesson, and the
site's own question id from ``#question-meta``.

No paid text is printed beyond a short prefix, and nothing is written to disk:
this is the before/after acceptance check for the parser phases.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from medo.parser import (
    explanation_blocks,
    extract_html_from_mhtml,
    parse_question_html,
)
from medo.text_utils import INVISIBLE_RE

SITE_ID_RE = re.compile(r'id="question-meta"[^>]*data-pqrfhskdjf="(\d+)"')
PREFIX = 70


def site_question_id(html: str) -> str:
    match = SITE_ID_RE.search(html)
    return match.group(1) if match else "—"


def report(path: Path, *, submitted: bool) -> dict:
    html = extract_html_from_mhtml(path.read_bytes())
    q = parse_question_html(html, submitted=submitted)
    blocks = explanation_blocks(html)
    text = q.explanation_text or ""
    leaked = [
        b["text"][:20] for b in blocks
        if b["hidden"] and b["text"] and b["text"][:20] in text
    ]
    return {
        "file": path.name,
        "site_id": site_question_id(html),
        "stem": (q.stem_text or "")[:PREFIX],
        "stem_len": len(q.stem_text or ""),
        "options": [(o.index, o.text[:24], o.vote_percent) for o in q.options],
        "correct": q.correct_index,
        "source": str(q.answer_source),
        "status": str(q.explanation_status),
        "expl_len": len(text),
        "invisible": len(INVISIBLE_RE.findall(
            "".join([q.stem_text or "", text, *(o.text for o in q.options)]))),
        "blocks": len(blocks),
        "dropped": sum(1 for b in blocks if b["hidden"]),
        "leaked": leaked,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default="data/diag", help="capture folder")
    parser.add_argument("--submitted", action="store_true", default=True,
                        help="parse the revealed (after-submit) state")
    args = parser.parse_args(argv)

    folder = Path(args.dir)
    files = sorted(folder.glob("*.mhtml"))
    if not files:
        print(f"no .mhtml files in {folder} — save one page there first")
        return 1

    for path in files:
        row = report(path, submitted=args.submitted)
        print(f"=== {row['file']}  (site id {row['site_id']})")
        print(f"  stem ({row['stem_len']} chars): {row['stem']!r}")
        print(f"  options: {row['options']}")
        print(f"  correct: {row['correct']}  source: {row['source']}")
        print(f"  explanation: {row['status']} len={row['expl_len']}"
              f"  invisible_left={row['invisible']}")
        print(f"  blocks: {row['blocks']} dropped: {row['dropped']}"
              f"  leaked: {row['leaked'] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
