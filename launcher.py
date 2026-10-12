"""
launcher.py
MedoOffline background launcher. Starts the local practice server and opens the browser.
Safely handles Windows pythonw execution by redirecting stdout/stderr to launcher.log.
"""
from __future__ import annotations

import os
import sys
import socket
import threading
import time
import webbrowser
from pathlib import Path

# Redirect stdout/stderr so pythonw never crashes on print/log_message
LOG_FILE = Path("E:/MedoOffline/launcher.log")
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


def _delayed_open(url: str, delay: float = 0.6) -> None:
    time.sleep(delay)
    webbrowser.open(url)


def main() -> None:
    url = "http://127.0.0.1:8080"
    print(f"[{time.ctime()}] Launching MedoOffline...")

    if is_port_in_use(8080):
        print("Port 8080 is already active, opening browser directly.")
        webbrowser.open(url)
        return

    # Start browser opener in background thread
    t = threading.Thread(target=_delayed_open, args=(url, 0.8), daemon=True)
    t.start()

    try:
        print("Starting MedoOffline server on port 8080...")
        serve(port=8080)
    except Exception as ex:
        print(f"Server exception: {ex}")
        webbrowser.open(url)


if __name__ == "__main__":
    main()
