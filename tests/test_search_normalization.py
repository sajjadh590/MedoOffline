"""
tests/test_search_normalization.py
One search canonicalisation, three languages.

Persian text reaches the app in several spellings (Arabic yeh/kaf, with or
without the half-space, Latin or Persian digits, plus the site's invisible
variation selectors).  Python normalises for hashing/export, the browser for
the search box, and Android for its own list search.  They must agree, so all
three are checked against ``tests/search_vectors.json``.

The Java copy is exercised by ``bash android/test.sh`` (TestMain embeds the
same cases); here Python and Node are checked, Node being skipped when it is
not installed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from medo.text_utils import normalize_search_text

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "tests" / "search_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", VECTORS, ids=[c["why"] for c in VECTORS])
def test_python_normalizes_the_shared_vectors(case):
    assert normalize_search_text(case["input"]) == case["expected"]


def test_python_tolerates_none_and_empty():
    assert normalize_search_text(None) == ""
    assert normalize_search_text("") == ""


def test_browser_normalizer_matches_the_same_vectors():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    script = (
        "const n = require(process.argv[1]);"
        "const v = require(process.argv[2]);"
        "const bad = v.filter(c => n(c.input) !== c.expected)"
        "  .map(c => JSON.stringify(c.input) + ' -> ' + JSON.stringify(n(c.input)));"
        "if (bad.length) { console.error(bad.join('\\n')); process.exit(1); }"
    )
    result = subprocess.run(
        [node, "-e", script,
         str(ROOT / "medo" / "web" / "normalize.js"),
         str(ROOT / "tests" / "search_vectors.json")],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
