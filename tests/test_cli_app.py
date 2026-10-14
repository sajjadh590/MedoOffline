"""
tests/test_cli_app.py
CLI registration tests: every command (including the legacy export-* aliases)
must be registered without crashing typer, and scrape requires a target.
"""
from typer.testing import CliRunner

from medo.cli import app

runner = CliRunner()

ALL_COMMANDS = [
    "login", "scrape", "discover", "serve",
    "build-pdf", "export-pdf", "build-web", "export-web",
    "stats", "verify", "import-fixtures",
]


def test_help_lists_every_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ALL_COMMANDS:
        assert name in result.output


def test_legacy_aliases_registered():
    names = {c.name for c in app.registered_commands}
    for name in ("build-web", "export-web", "build-pdf", "export-pdf"):
        assert name in names, f"missing command: {name}"


def test_alias_and_primary_share_callback():
    by_name = {c.name: c for c in app.registered_commands}
    assert by_name["build-web"].callback is by_name["export-web"].callback
    assert by_name["build-pdf"].callback is by_name["export-pdf"].callback


def test_scrape_requires_target(tmp_path):
    result = runner.invoke(app, ["scrape", "--db", str(tmp_path / "t.db")])
    assert result.exit_code == 1
    assert "--topic" in result.output
