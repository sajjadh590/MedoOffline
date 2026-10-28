"""tests/test_audit_push.py — the rules that keep private data out of git."""
import importlib.util
from pathlib import Path

import pytest


def _audit_module():
    path = (Path(__file__).resolve().parents[1]
            / "scripts" / "dev" / "audit_push.py")
    spec = importlib.util.spec_from_file_location("audit_push", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def audit():
    return _audit_module()


def test_private_paths_names_and_big_files_are_refused(audit, tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "medo.db").write_bytes(b"x")
    (tmp_path / "sess").mkdir()
    (tmp_path / "sess" / "storage_state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "page.mhtml").write_text("x", encoding="utf-8")
    (tmp_path / "big.py").write_text("x" * 1_000_001, encoding="utf-8")

    findings = audit.audit_paths(tmp_path, [
        "data/medo.db", "sess/storage_state.json", "page.mhtml", "big.py"])
    reasons = [f.split(": ", 1)[1] for f in findings]
    assert "under a private folder" in reasons[0]
    assert "session/cookie file" in reasons[1]
    assert "private/generated file type" in reasons[2]
    assert reasons[3].endswith("MB (limit 1 MB)")


def test_long_persian_prose_is_refused(audit, tmp_path):
    (tmp_path / "lesson.md").write_text("س" * 320, encoding="utf-8")
    (tmp_path / "notes.md").write_text("س" * 40, encoding="utf-8")

    findings = audit.audit_paths(tmp_path, ["lesson.md", "notes.md"])
    assert len(findings) == 1
    assert "Persian letters on one line" in findings[0]
    assert "س" * 20 not in findings[0]          # the message never quotes content


def test_text_copied_from_a_private_capture_is_refused(audit, tmp_path,
                                                       monkeypatch):
    # Wrapped lines stay under the prose limit; only the capture match catches it.
    lesson = "\n".join(["متن طولانی درس شمارهٔ آزمایشی" * 3] * 10)
    (tmp_path / "stolen.md").write_text(lesson, encoding="utf-8")
    monkeypatch.setattr(audit, "_capture_texts",
                        lambda root: [("real.mhtml", audit._bare(lesson))])

    findings = audit.audit_paths(tmp_path, ["stolen.md"])
    assert findings == ["stolen.md: text copied from data/diag/real.mhtml"]


def test_git_listing_reads_the_repository(audit):
    root = Path(__file__).resolve().parents[1]
    if not (root / ".git").exists():
        pytest.skip("exported tree, not a git checkout")
    files = audit.tracked_files(root)
    assert "medo/cli.py" in files
    assert not any(f.startswith("data/") for f in files)


def test_an_export_is_walked_instead_of_the_parent_repo(audit, tmp_path):
    # No .git inside: this is what `git archive` hands a new machine.
    (tmp_path / "medo.py").write_text("x", encoding="utf-8")
    assert audit.tracked_files(tmp_path) is None
    assert audit.all_files(tmp_path) == ["medo.py"]
