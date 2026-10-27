"""End-to-end login detection against a local fake site.

The real site only confirms a login over SMS, so this stands in for it:
``/login`` posts an OTP-less login, rotates ``sessionid`` server-side and
lands the browser on ``/`` (home), **not** on the dashboard.  That is the
case the old detector missed — the URL never contained ``/dashboard`` — while
the account was in fact logged in.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from medo import auth


@pytest.fixture()
def fake_site():
    state = {"logins": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # keep pytest output clean
            pass

        def _send(self, code: int, body: str, headers: dict | None = None):
            data = body.encode("utf-8")
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path == "/login-quiet":
                self._send(200, "<html><body><h1>ورود</h1></body></html>")
                return
            if path == "/login":
                self._send(200, """<html><body><h1>ورود</h1>
<script>
setTimeout(() => {
  fetch('/complete', {method: 'POST'})
    .then(() => { window.location.href = '/'; });
}, 700);
</script></body></html>""")
                return
            if path == "/":
                self._send(200, "<html><body>home</body></html>")
                return
            if path == "/dashboard/welcome":
                cookie = self.headers.get("Cookie", "")
                if "sessionid=user-" in cookie:
                    self._send(200, "<html><body>dashboard</body></html>")
                else:
                    self._send(302, "", {"Location": "/login?next=/dashboard/welcome"})
                return
            self._send(404, "nope")

        def do_POST(self):
            if self.path.split("?")[0] == "/complete":
                state["logins"] += 1
                new_id = f"user-{state['logins']}"
                self._send(200, "{}", {
                    "Set-Cookie": f"sessionid={new_id}; Path=/; HttpOnly",
                    "Content-Type": "application/json",
                })
                return
            self._send(404, "nope")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_login_landing_on_home_is_detected(fake_site, tmp_path, monkeypatch):
    monkeypatch.setattr(auth.applog, "install_tee", lambda: None)
    dest = tmp_path / "storage_state.json"

    saved = auth.login_interactive(
        dest,
        url=f"{fake_site}/login",
        timeout_seconds=25,
        dashboard=f"{fake_site}/dashboard/welcome",
        headless=True,
    )

    assert saved == dest, "login must be confirmed even when the site lands on /"
    cookies = {c["name"]: c["value"]
               for c in json.loads(Path(dest).read_text(encoding="utf-8"))["cookies"]}
    assert cookies.get("sessionid", "").startswith("user-")


def test_guest_redirect_is_never_saved(fake_site, tmp_path, monkeypatch):
    """No login happens: the detector must time out and keep the file absent."""
    monkeypatch.setattr(auth.applog, "install_tee", lambda: None)
    dest = tmp_path / "storage_state.json"

    saved = auth.login_interactive(
        dest,
        url=f"{fake_site}/login-quiet",
        timeout_seconds=5,
        dashboard=f"{fake_site}/dashboard/welcome",
        headless=True,
    )

    assert saved is None
    assert not dest.exists()
