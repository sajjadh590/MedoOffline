"""
tests/conftest.py
Make a clean clone testable.

The parser tests read the synthetic fixtures, but those are generated files and
are git-ignored on purpose (a real capture contains the site's paid text).  This
session fixture generates them once when they are missing, so a fresh clone with
a new venv runs green instead of failing on an absent file.  Real captures are
never generated: tests that need one skip themselves.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
SYNTHETIC = ("before.mhtml", "after.mhtml")


@pytest.fixture(scope="session", autouse=True)
def synthetic_fixtures() -> None:
    missing = [name for name in SYNTHETIC if not (FIXTURES / name).is_file()]
    if not missing:
        return
    script = ROOT / "scripts" / "make_fixtures.py"
    if not script.is_file():
        pytest.skip(f"missing {', '.join(missing)} and no generator script")
    subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
