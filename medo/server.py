"""
medo/server.py
Lightweight offline local HTTP server for interactive question practice
and background live scraping management.
"""
from __future__ import annotations

import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from medo.auth import has_storage_state, login_interactive
from medo.db import (
    DEFAULT_DB_PATH,
    get_all_questions,
    get_connection,
    get_database_stats,
    set_user_answer,
    toggle_bookmark,
)
from medo.exporter import export_web_bundle
from medo.scraper import Harvester

WEB_DIR = Path(__file__).parent / "web"
ASSETS_DIR = Path("data/assets")


class ScraperTaskController:
    """Manages background scraping execution and progress reporting."""

    def __init__(self):
        self.is_running = False
        self.status = "idle"
        self.topic_id = 0
        self.current_page = 0
        self.scraped_count = 0
        self.logs: list[str] = []
        self.harvester: Harvester | None = None
        self.thread: threading.Thread | None = None

    def start(
        self,
        topic_id: int,
        start_page: int | None = None,
        max_pages: int | None = None,
        db_path: Path | str = DEFAULT_DB_PATH,
    ) -> tuple[bool, str]:
        if self.is_running:
            return False, "عملیات استخراج در حال حاضر در حال اجرا است."

        self.is_running = True
        self.status = "running"
        self.topic_id = topic_id
        self.current_page = start_page or 1
        self.scraped_count = 0
        self.logs = [f"شروع استخراج سوالات مبحث {topic_id}..."]

        def _on_progress(page: int, qid: int, preview: str):
            self.current_page = page
            self.scraped_count += 1
            msg = f"صفحه {page}: سوال {qid} با موفقیت ذخیره شد ({preview}...)"
            self.logs.append(msg)
            if len(self.logs) > 60:
                self.logs.pop(0)

        def _worker():
            try:
                self.harvester = Harvester(
                    db_path=db_path,
                    on_progress=_on_progress,
                )
                res = self.harvester.scrape_topic(topic_id, start_page=start_page, max_pages=max_pages)
                self.status = "completed"
                self.logs.append(f"استخراج مبحث با موفقیت به پایان رسید! {res.get('scraped_count', 0)} سوال ذخیره شد.")
                # Auto-update static export
                try:
                    export_web_bundle(db_path=db_path)
                    self.logs.append("بانک آفلاین و وب‌اپلیکیشن به‌روزرسانی شد.")
                except Exception as ex:
                    self.logs.append(f"خطا در به‌روزرسانی خروجی وب: {ex}")
            except Exception as ex:
                self.status = "error"
                self.logs.append(f"خطا در استخراج: {ex}")
            finally:
                self.is_running = False

        self.thread = threading.Thread(target=_worker, daemon=True)
        self.thread.start()
        return True, "عملیات استخراج آغاز شد."

    def stop(self) -> tuple[bool, str]:
        if self.harvester and self.is_running:
            self.harvester._interrupted = True
            self.status = "stopping"
            self.logs.append("درخواست توقف اسکریپر ارسال شد. در حال اتمام سوال جاری...")
            return True, "درخواست توقف ارسال شد."
        return False, "اسکریپر در حال اجرا نیست."

    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "status": self.status,
            "topic_id": self.topic_id,
            "current_page": self.current_page,
            "scraped_count": self.scraped_count,
            "logs": self.logs[-25:],
        }


# Global background scraper instance
SCRAPER_CONTROLLER = ScraperTaskController()


class MedoRequestHandler(SimpleHTTPRequestHandler):
    """HTTP request handler serving static files and API endpoints."""

    def __init__(self, *args, db_path=DEFAULT_DB_PATH, **kwargs):
        self.db_path = db_path
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/questions":
            conn = get_connection(self.db_path)
            questions = get_all_questions(conn)
            conn.close()
            self._send_json(questions)
            return

        if parsed.path == "/api/stats":
            conn = get_connection(self.db_path)
            stats = get_database_stats(conn)
            conn.close()
            self._send_json(stats)
            return

        if parsed.path == "/api/tree":
            from medo.db import get_category_progress, get_category_tree

            conn = get_connection(self.db_path)
            tree = get_category_tree(conn)
            progress = get_category_progress(conn)
            conn.close()

            def _decorate(nodes: list[dict]) -> list[dict]:
                for n in nodes:
                    n["progress"] = progress.get(n["source_url"], {})
                    n["partial"] = bool(
                        n["progress"].get("status") not in {"completed"}
                        and n["progress"].get("scraped", 0) > 0
                    )
                    _decorate(n.get("children", []))
                return nodes

            self._send_json(_decorate(tree))
            return

        if parsed.path == "/api/auth-status":
            self._send_json({
                "authenticated": has_storage_state(),
            })
            return

        if parsed.path == "/api/scrape/status":
            self._send_json(SCRAPER_CONTROLLER.get_status())
            return

        if parsed.path.startswith("/assets/"):
            # Serve local media assets
            rel_asset = parsed.path.replace("/assets/", "", 1)
            asset_file = ASSETS_DIR / rel_asset
            if asset_file.is_file():
                self.send_response(200)
                self.send_header("Content-Type", "image/webp" if asset_file.suffix == ".webp" else "image/png")
                self.send_header("Content-Length", str(asset_file.stat().st_size))
                self.end_headers()
                self.wfile.write(asset_file.read_bytes())
                return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            data = {}

        if parsed.path == "/api/answer":
            qid = data.get("question_id")
            opt = data.get("selected_option")
            if qid and opt:
                conn = get_connection(self.db_path)
                set_user_answer(conn, qid, opt)
                conn.close()
                self._send_json({"ok": True})
                return

        if parsed.path == "/api/bookmark":
            qid = data.get("question_id")
            if qid:
                conn = get_connection(self.db_path)
                new_state = toggle_bookmark(conn, qid)
                conn.close()
                self._send_json({"ok": True, "bookmarked": new_state})
                return

        if parsed.path == "/api/login":
            def _login_task():
                try:
                    login_interactive()
                except Exception as ex:
                    print(f"Login error: {ex}")
            threading.Thread(target=_login_task, daemon=True).start()
            self._send_json({"ok": True, "message": "مرورگر برای لاگین باز شد."})
            return

        if parsed.path == "/api/scrape/start":
            topic_id = int(data.get("topic_id", 79))
            start_page = int(data["start_page"]) if data.get("start_page") else None
            max_pages = int(data["max_pages"]) if data.get("max_pages") else None

            ok, msg = SCRAPER_CONTROLLER.start(
                topic_id=topic_id,
                start_page=start_page,
                max_pages=max_pages,
                db_path=self.db_path,
            )
            self._send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/scrape/stop":
            ok, msg = SCRAPER_CONTROLLER.stop()
            self._send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/export-web":
            try:
                export_web_bundle(db_path=self.db_path)
                self._send_json({"ok": True, "message": "خروجی وب به‌روزرسانی شد."})
            except Exception as ex:
                self._send_json({"ok": False, "message": str(ex)})
            return

        self.send_error(404, "Endpoint not found")

    def _send_json(self, payload):
        blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)


def serve(port: int = 8080, db_path: Path | str = DEFAULT_DB_PATH):
    """Start local web server."""
    handler_factory = lambda *args, **kwargs: MedoRequestHandler(*args, db_path=db_path, **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_factory)
    print(f"\n[+] MedoOffline web server running at: http://127.0.0.1:{port}")
    print("[*] Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.server_close()
