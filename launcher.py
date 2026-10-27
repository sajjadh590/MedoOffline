"""
launcher.py
MedoOffline background launcher. Starts the local practice server and opens the browser.
Safely handles Windows pythonw execution by redirecting stdout/stderr to launcher.log.
"""
from __future__ import annotations

import json
import os
import sys
import socket
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path

from medo.paths import PROJECT_ROOT

# Redirect stdout/stderr so pythonw never crashes on print/log_message
LOG_FILE = PROJECT_ROOT / "launcher.log"
# 8080 first, then a few neighbours: another local tool may already own it,
# and opening the user's browser on that tool's page would be confusing.
PORT_CANDIDATES = (8080, 8081, 8082, 8083, 8084)
try:
    log_fp = open(LOG_FILE, "a", encoding="utf-8")
    sys.stdout = log_fp
    sys.stderr = log_fp
except Exception:
    pass

from medo.server import serve


def is_port_in_use(port: int = 8080) -> bool:
    """Check if server is already running on the given port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _delayed_open(url: str, delay: float, state: dict) -> None:
    """Open the browser once, unless the failure path already did.

    The starter used to open a second tab when the server failed instantly:
    this thread still fired at 1.2 s after ``except`` had opened its own.
    """
    time.sleep(delay)
    if state["opened"]:
        return
    state["opened"] = True
    webbrowser.open(url)


def _is_medo_server(port: int) -> bool:
    """True when the process on `port` answers like this app."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/stats", timeout=1.5
        ) as response:
            data = json.load(response)
    except Exception:
        return False
    return isinstance(data, dict) and "total_questions" in data


def pick_port() -> tuple[int, bool]:
    """(port, already_running) — a free port, or this app's live server."""
    for port in PORT_CANDIDATES:
        if not is_port_in_use(port):
            return port, False
        if _is_medo_server(port):
            return port, True
        print(f"Port {port} is used by another program; trying the next one.")
    raise SystemExit("No free local port in 8080-8084.")


def main() -> None:
    print(f"[{time.ctime()}] Launching MedoOffline...")
    port, running = pick_port()
    # The launch token makes every start a fresh document URL, so a browser
    # cache entry can never mask a patched UI or a newly extracted archive.
    url = f"http://127.0.0.1:{port}/?launch={int(time.time())}"

    if running:
        print(f"MedoOffline already runs on port {port}; opening the browser.")
        webbrowser.open(url)
        return

    # Start browser opener in background thread
    open_state = {"opened": False}
    t = threading.Thread(target=_delayed_open, args=(url, 1.2, open_state),
                         daemon=True)
    t.start()

    try:
        print(f"Starting MedoOffline server on port {port}...")
        serve(port=port)
    except Exception as ex:
        print(f"Server exception: {ex}\n{traceback.format_exc()}")
        if not open_state["opened"]:
            open_state["opened"] = True
            webbrowser.open(url)


if __name__ == "__main__":
    main()
