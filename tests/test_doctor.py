"""tests/test_doctor.py — the pre-flight verdict, without a network."""
from medo import doctor


def _by_name(result):
    return {c["name"]: c for c in result["checks"]}


def test_network_failure_is_a_red_verdict(monkeypatch):
    monkeypatch.setattr(doctor, "check_network", lambda host=doctor.HOST: [
        {"name": "proxy", "ok": True, "detail": ""},
        {"name": "dns", "ok": True, "detail": ""},
        {"name": "tls", "ok": False, "detail": "cut"},
    ])
    result = doctor.run_checks()
    assert result["verdict"] == "network"
    assert result["ok"] is False
    assert doctor.HELP_TEXT in result["help"]


def test_missing_login_is_yellow_not_red(monkeypatch):
    monkeypatch.setattr(doctor, "check_network", lambda host=doctor.HOST: [
        {"name": "proxy", "ok": True, "detail": ""},
        {"name": "dns", "ok": True, "detail": ""},
        {"name": "tls", "ok": True, "detail": ""},
    ])
    monkeypatch.setattr(doctor, "check_login",
                        lambda *a, **k: {"name": "login", "ok": False, "detail": ""})
    result = doctor.run_checks()
    assert result["verdict"] == "auth"
    assert result["ok"] is True
    assert "وارد شو" in result["message"]


def test_all_green_says_ready(monkeypatch):
    monkeypatch.setattr(doctor, "check_network", lambda host=doctor.HOST: [
        {"name": "proxy", "ok": True, "detail": ""},
        {"name": "dns", "ok": True, "detail": ""},
        {"name": "tls", "ok": True, "detail": ""},
    ])
    monkeypatch.setattr(doctor, "check_login",
                        lambda *a, **k: {"name": "login", "ok": True, "detail": ""})
    result = doctor.run_checks()
    assert result["verdict"] == "ready"
    assert result["message"] == "آماده استخراج."


def test_identity_check_reads_the_archive(tmp_path):
    from medo.db import get_connection, init_db

    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()
    check = doctor.check_identity(db)
    assert check["ok"] is True
    assert "خالی" in check["detail"]
