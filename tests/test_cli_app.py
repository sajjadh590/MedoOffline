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
    "stats", "verify", "import-fixtures", "doctor", "keystore-backup",
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


def test_keystore_backup_keeps_the_key_and_its_digest(tmp_path):
    """Without this key an installed app can never be updated, only replaced."""
    import hashlib
    import zipfile

    key = tmp_path / "medo-debug.keystore"
    key.write_bytes(b"fake-key-bytes")
    out = tmp_path / "backup"
    result = runner.invoke(app, ["keystore-backup", "--keystore", str(key),
                                 "--output", str(out)])
    assert result.exit_code == 0, result.output

    zips = list(out.glob("*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as zf:
        assert zf.read("medo-debug.keystore") == b"fake-key-bytes"
        assert zf.read("SHA-256.txt").decode().strip() == \
            hashlib.sha256(b"fake-key-bytes").hexdigest()


def test_readme_only_mentions_flags_the_cli_defines():
    """Documentation drift: a renamed flag must break the build, not the user."""
    import re
    from pathlib import Path

    import typer

    root = typer.main.get_command(app)
    known = {"--help"}
    for cmd in root.commands.values():
        for param in cmd.params:
            known.update(param.opts)
            known.update(getattr(param, "secondary_opts", ()) or ())
    # Flags of the tools the README tells you to run, not of this CLI.
    known.update({"--no-proxy-server", "--check"})

    readme = (Path(__file__).resolve().parent.parent / "README.md").read_text(
        encoding="utf-8")
    mentioned = set(re.findall(r"(?<![\w-])--[a-z][a-z0-9-]+", readme))
    unknown = sorted(mentioned - known)
    assert not unknown, f"README mentions flags the CLI does not define: {unknown}"
