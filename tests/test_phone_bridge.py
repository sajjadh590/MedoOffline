"""tests/test_phone_bridge.py

The desktop app's phone bridge: the LAN share server, the artifacts it hands
out, and the guarded APK build.  These are the paths behind the «گوشی» panel —
if they regress, the phone can no longer receive a bank or an updated app.
"""
from __future__ import annotations

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from medo.db import get_connection, init_db
from medo.server import MedoRequestHandler, PhoneBridge


@pytest.fixture()
def live_server(tmp_path):
    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()

    handler = lambda *a, **kw: MedoRequestHandler(*a, db_path=db, **kw)  # noqa: E731
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], db
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(port: int, path: str):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        raw = resp.read()
        return resp.status, dict(resp.getheaders()), raw
    finally:
        conn.close()


def _post(port: int, path: str, payload: dict | None = None):
    body = json.dumps(payload or {}).encode("utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("POST", path, body=body,
                     headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


def test_phone_status_shape(live_server):
    port, _ = live_server
    status, _, raw = _get(port, "/api/phone")
    data = json.loads(raw.decode("utf-8"))
    assert status == 200
    assert data["running"] is False
    assert set(("port", "lan", "suggested_url", "bank", "apk", "building")) <= set(data)
    assert data["suggested_url"].startswith("http://")


def test_bridge_start_and_stop_on_ephemeral_port(tmp_path):
    db = tmp_path / "medo.db"
    conn = get_connection(db)
    init_db(conn)
    conn.close()

    bridge = PhoneBridge(port=0)
    ok, message = bridge.start(db_path=db)
    try:
        assert ok, message
        info = bridge.status(db)
        assert info["running"] is True
        assert info["port"] > 0
        assert info["urls"] and str(info["port"]) in info["urls"][0]
        assert info["suggested_url"].endswith(str(info["port"]))
    finally:
        stopped, _ = bridge.stop()
    assert stopped
    assert bridge.status(db)["running"] is False


def test_apk_download_is_an_attachment(live_server, tmp_path, monkeypatch):
    port, _ = live_server
    apk = tmp_path / "medo.apk"
    apk.write_bytes(b"PK\x03\x04fake-apk")
    monkeypatch.setattr(PhoneBridge, "_apk_path", staticmethod(lambda: apk))

    status, headers, raw = _get(port, "/api/phone/apk")
    assert status == 200
    assert raw == b"PK\x03\x04fake-apk"
    assert headers["Content-Type"] == "application/vnd.android.package-archive"
    assert 'filename="medo.apk"' in headers["Content-Disposition"]


def test_apk_download_reports_missing_file(live_server, tmp_path, monkeypatch):
    port, _ = live_server
    monkeypatch.setattr(PhoneBridge, "_apk_path",
                        staticmethod(lambda: tmp_path / "absent.apk"))
    status, _, raw = _get(port, "/api/phone/apk")
    data = json.loads(raw.decode("utf-8"))
    assert status == 200
    assert data["ok"] is False


def test_build_apk_refuses_double_start(live_server):
    port, _ = live_server
    from medo.server import PHONE_BRIDGE

    PHONE_BRIDGE.building = True
    try:
        status, data = _post(port, "/api/phone/build-apk")
    finally:
        PHONE_BRIDGE.building = False
    assert status == 200
    assert data["ok"] is False
    assert "جریان" in data["message"]
