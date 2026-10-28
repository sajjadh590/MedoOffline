#!/usr/bin/env python
"""Export HEAD to build/clean-clone and prove a fresh clone works.

A working tree can hide problems: generated fixtures, an ignored module, a
file that was never added.  This script exports exactly what ``git archive``
would give a new machine, runs the pre-push audit there, then the test suite
with the current interpreter (no install step, matching a fresh clone).

    python scripts/dev/clean_clone_test.py

Exit code 0 only when both the audit and pytest pass.  The export is removed
first and left in place afterwards for inspection (build/ is git-ignored).
"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "clean-clone"


def run(cmd: list[str], cwd: Path, label: str) -> int:
    print(f"\n=== {label}: {' '.join(cmd)} (in {cwd.name})")
    result = subprocess.run(cmd, cwd=str(cwd), env={**os.environ,
                                                    "PYTHONPATH": str(cwd)})
    print(f"=== {label}: exit {result.returncode}")
    return result.returncode


def main() -> int:
    archive = subprocess.run(
        ["git", "-C", str(ROOT), "archive", "--format=tar", "HEAD"],
        capture_output=True, check=False)
    if archive.returncode != 0:
        print("[!] git archive failed:", archive.stderr.decode("utf-8", "replace")[:200])
        return 2
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(OUT)

    audit = run([sys.executable, "scripts/dev/audit_push.py"], OUT, "audit")
    tests = run([sys.executable, "-m", "pytest", "-q"], OUT, "pytest")
    print(f"\n[clean-clone] audit={audit} pytest={tests} — "
          + ("clean" if audit == 0 and tests == 0 else "FAILED"))
    return 0 if audit == 0 and tests == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
