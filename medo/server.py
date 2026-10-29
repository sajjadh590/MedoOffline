"""
medo/server.py
Lightweight offline local HTTP server for interactive question practice
and background live scraping management.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from medo import applog
from medo.auth import has_storage_state, login_interactive, session_status
from medo.db import (
    DEFAULT_DB_PATH,
    LOGIN_RESUME_STATUSES,
    get_all_questions,
    get_connection,
    get_database_stats,
    get_question_by_id,
    get_questions_light,
    clear_run_request,
    count_needing_repair,
    count_node_questions,
    count_untagged_questions,
    declared_questions,
    get_scrape_state,
    get_target_state,
    get_usage_summary,
    update_micro_state,
    load_run_request,
    pending_resume_targets,
    reconcile_categories,
    record_usage,
    save_run_request,
    set_bookmark,
    set_user_answer,
    subtopic_targets,
    tag_id_of,
    toggle_bookmark,
)
from medo.exporter import export_web_bundle
from medo.paths import DEFAULT_ASSETS_DIR as ASSETS_DIR
from medo.paths import DIST_DIR, PROJECT_ROOT
from medo.scraper import Harvester

WEB_DIR = Path(__file__).parent / "web"


def _mb(byte_count: int | float) -> str:
    """Bytes as a short Persian unit string (KB under a megabyte)."""
    n = max(0.0, float(byte_count or 0))
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} مگابایت"
    return f"{n / 1024:.0f} کیلوبایت"


class ScraperTaskController:
    """Manages background scraping execution and progress reporting."""

    def __init__(self):
        # ThreadingHTTPServer runs every request in its own thread: the
        # check-then-set of the single job slot needs a real lock or two
        # clicks can both pass the check.
        self._job_lock = threading.Lock()
        self.job_id: int | None = None
        self.is_running = False
        self.status = "idle"
        self.topic_id = 0
        self.current_page = 0
        self.scraped_count = 0
        self.logs: list[str] = []
        # Monotonic count of every line ever pushed.  The UI polls a sliding
        # window (last 25 lines), so it cannot use ``len(logs)`` to know what
        # is new: once the window was full the panel stopped updating.
        self.log_seq = 0
        self.harvester: Harvester | None = None
        self.thread: threading.Thread | None = None
        self.stored_total_before = 0  # questions stored BEFORE this run
        self.db_path: Path | str = DEFAULT_DB_PATH
        # The request that last hit the login wall, so a confirmed login can
        # continue the exact same download without another click.
        self.last_request: dict | None = None
        self.login_in_progress = False
        # Internet usage: the live harvester owns the counter; this keeps the
        # finished run's totals for the UI after the browser is closed.
        self.run_started_iso = ""
        self.run_bytes = 0
        self.run_questions = 0
        # Set when a login succeeds while a run is still winding down; the
        # worker continues the download itself once it reaches ``finally``.
        self._resume_pending = False
        # What the current job is: scrape / answers / tags. The dock and the
        # modal use it to describe the run correctly.
        self.job = ""
        self.cancel_requested = False
        # Sum of declared-but-missing questions for the selected download; the
        # harvester turns it into a live ETA.
        self.expected_questions = 0

    def _push(self, msg: str) -> None:
        """One log entry for both audiences: the UI panel (memory) and the
        durable timestamped file (logs/medo.log)."""
        self.logs.append(msg)
        self.log_seq += 1
        if len(self.logs) > 60:
            self.logs.pop(0)
        applog.log(f"[ui] {msg}")

    @staticmethod
    def _refresh_fallback_nodes(db_path) -> None:
        """Re-group the archive after a run.

        Every extracted topic must be visible in the tree even when the
        site's discovery failed, and a re-discovered tree renames/moves
        sections: the stored ``category_path`` has to follow, or the sidebar
        and the question badge disagree about where a question belongs.
        """
        try:
            conn = get_connection(db_path)
            try:
                reconcile_categories(conn)
            finally:
                conn.close()
        except Exception as ex:
            applog.log(f"[ui] category reconcile failed: {ex}")

    @staticmethod
    def _normalize_targets(
        topic_id: int, topic_ids: list[int] | None,
        tag_id: int | None, subtopic_title: str | None,
        targets: list[dict] | None,
    ) -> list[dict]:
        """One uniform, de-duplicated list of scrape targets.

        A target is a whole topic or one subtopic of it:
        ``{topic_id, tag_id, subtopic_title}``.  The old topic-id list is
        still accepted; the rich form is what lets a group of subtopics be
        downloaded in one run.
        """
        wanted: list[dict] = []
        if targets:
            for t in targets:
                try:
                    tid = int(t.get("topic_id") or 0)
                except (TypeError, ValueError):
                    continue
                if tid <= 0:
                    continue
                tag = t.get("tag_id")
                try:
                    tag = int(tag) if tag not in (None, "") else None
                except (TypeError, ValueError):
                    tag = None
                wanted.append({
                    "topic_id": tid, "tag_id": tag,
                    "subtopic_title": (t.get("subtopic_title") or None),
                })
        else:
            ids = [int(t) for t in (topic_ids or [topic_id]) if int(t) > 0] or [int(topic_id)]
            tag = int(tag_id) if tag_id not in (None, "") else None
            for i, tid in enumerate(ids):
                wanted.append({
                    "topic_id": tid,
                    # The explicit tag belongs to the single requested topic;
                    # a batch of topic ids stays whole-topic.
                    "tag_id": tag if (tag is not None and i == 0) else None,
                    "subtopic_title": subtopic_title if i == 0 else None,
                })
        seen: set[tuple[int, int | None]] = set()
        unique: list[dict] = []
        for t in wanted:
            key = (t["topic_id"], t["tag_id"])
            if key in seen:
                continue
            seen.add(key)
            unique.append(t)
        return unique

    @staticmethod
    def _expand_subtopics(db_path: Path | str, wanted: list[dict]) -> list[dict]:
        """Queue a topic's subtopics instead of one unfiltered topic pass.

        A whole-topic page never says which subtopic a question belongs to, so
        that pass stored every question with tag NULL and left the sidebar's
        subtopics empty until a separate backfill ran (and a re-download then
        flipped tags back and forth).  When the tree knows the subtopics, each
        one is queued as its own target: questions land under their subtopic in
        the same pass, and a finished subtopic is skipped on the next run.
        """
        try:
            conn = get_connection(db_path)
        except Exception as ex:
            applog.log(f"[scrape] subtopic expansion skipped: {ex}")
            return wanted
        try:
            expanded: list[dict] = []
            for target in wanted:
                if target["tag_id"] is not None:
                    expanded.append(target)
                    continue
                subs = subtopic_targets(conn, target["topic_id"])
                if subs:
                    applog.log(f"[scrape] topic {target['topic_id']}: "
                               f"{len(subs)} subtopic(s) — downloading one by one")
                    expanded.extend(subs)
                else:
                    expanded.append(target)
            return expanded
        except Exception as ex:
            applog.log(f"[scrape] subtopic expansion failed: {ex}")
            return wanted
        finally:
            conn.close()

    def start(
        self,
        topic_id: int = 0,
        start_page: int | None = None,
        max_pages: int | None = None,
        db_path: Path | str = DEFAULT_DB_PATH,
        resume: bool = False,
        topic_ids: list[int] | None = None,
        tag_id: int | None = None,
        subtopic_title: str | None = None,
        targets: list[dict] | None = None,
    ) -> tuple[bool, str]:
        wanted = self._normalize_targets(topic_id, topic_ids, tag_id,
                                         subtopic_title, targets)
        if not wanted:
            return False, "مبحثی برای استخراج انتخاب نشده است."

        # No run should end with 0 saved because the path to the site is
        # broken: say which part is broken instead of starting anyway.
        from medo import doctor
        network_ok, verdict = doctor.quick_gate(db_path)
        if not network_ok:
            self._push(verdict)
            applog.log(f"[doctor] scrape blocked: {verdict}")
            return False, verdict

        # A finished run's thread may still be exporting the bundle; starting
        # a second one then opened two browsers on the same DB.
        with self._job_lock:
            if self.is_running or (self.thread is not None and self.thread.is_alive()):
                return False, "عملیات استخراج در حال حاضر در حال اجرا است."
            self.is_running = True   # the slot is taken under the lock

        wanted = self._expand_subtopics(db_path, wanted)
        multi = len(wanted) > 1
        # Prefer the tree's own subtitle: it is what the sidebar shows and it
        # is always correctly encoded, unlike a client-supplied string.
        try:
            from medo.db import category_title
            conn = get_connection(db_path)
            try:
                for t in wanted:
                    if t["tag_id"] is not None:
                        t["subtopic_title"] = (category_title(
                            conn, t["topic_id"], t["tag_id"])
                            or t["subtopic_title"])
            finally:
                conn.close()
        except Exception as ex:
            applog.log(f"[scrape] could not resolve subtopic titles: {ex}")

        self.status = "running"
        self.job = "scrape"
        self.cancel_requested = False
        self.topic_id = wanted[0]["topic_id"]
        self.current_page = start_page or 1
        self.scraped_count = 0
        self.db_path = db_path
        self.stored_total_before = self._count_questions(db_path)
        self.run_started_iso = datetime.now(timezone.utc).isoformat()
        self.run_bytes = 0
        self.run_questions = 0
        self.logs = []
        self.log_seq = 0
        self._resume_pending = False
        if multi:
            self._push(f"شروع استخراج {len(wanted)} مبحث انتخاب‌شده...")
        elif wanted[0]["tag_id"] is not None:
            self._push(f"شروع استخراج زیرمبحث «{wanted[0]['subtopic_title'] or wanted[0]['tag_id']}»...")
        else:
            self._push(f"شروع استخراج سوالات مبحث {wanted[0]['topic_id']}...")
        applog.log(f"[scrape] start targets={wanted} start_page={start_page} "
                   f"max_pages={max_pages} resume={resume} db={db_path}")

        def _on_progress(page: int, qid: int, preview: str):
            self.current_page = page
            self.scraped_count += 1
            self._push(f"صفحه {page}: سوال {qid} با موفقیت ذخیره شد ({preview}...)")

        def _resume_start(target: dict) -> int | None:
            if not resume:
                return start_page
            conn = get_connection(db_path)
            try:
                st = get_target_state(conn, target["topic_id"], target["tag_id"])
            finally:
                conn.close()
            if st and st.get("last_page", 0) > 0:
                effective = int(st["last_page"]) + 1
                self._push(f"ادامه از صفحه {effective} "
                           f"(آخرین صفحه ذخیره‌شده: {int(st['last_page'])})")
                return effective
            return start_page

        def _worker():
            total_saved = 0
            status = "completed"
            last_detail = ""
            try:
                self.harvester = Harvester(
                    db_path=db_path,
                    on_progress=_on_progress,
                )
                expected = 0
                for target in wanted:
                    done, _state, declared = self._target_progress(
                        target["topic_id"], target["tag_id"])
                    if declared:
                        expected += max(int(declared) - int(done), 0)
                self.expected_questions = expected
                self.harvester.expected_total = expected
                for index, target in enumerate(wanted):
                    if self.harvester._interrupted:
                        status = "interrupted"
                        break
                    tid = target["topic_id"]
                    tag = target["tag_id"]
                    label = (target.get("subtopic_title")
                             or (f"زیرمبحث {tag}" if tag else f"مبحث {tid}"))
                    self.topic_id = tid
                    if multi:
                        # «مبحث 4 از 13» + what is already in hand: the log used
                        # to show a bare topic id with no count and no progress.
                        self._push(f"مبحث {index + 1} از {len(wanted)} — «{label}»"
                                   + (f" · تا اینجا {total_saved} سوال"
                                      if index else ""))
                    done, done_status, declared = self._target_progress(tid, tag)
                    if (tag and done_status == "completed"
                            and declared and done < declared):
                        # A run that started ahead of the page (the session's
                        # remembered position) then saw the disabled next
                        # button and recorded a half-downloaded subtopic as
                        # complete.  Re-queue it from page 1.
                        self._reset_truncated_target(tid, tag)
                        done_status = "paused"
                        self._push(f"  ↳ «{label}» ناقص ذخیره شده بود "
                                   f"({done} از {declared}) — از ابتدا دوباره "
                                   f"دانلود می‌شود.")
                    if tag and done and done_status == "completed":
                        self._push(f"  ↳ «{label}» قبلاً کامل دانلود شده "
                                   f"({done} سوال) — دوباره دانلود نمی‌شود.")
                        continue
                    effective_start = _resume_start(target)
                    res = self.harvester.scrape_topic(
                        tid,
                        start_page=effective_start,
                        max_pages=None if multi else max_pages,
                        tag_id=tag,
                        subtopic_title=target["subtopic_title"],
                    )
                    saved_now = int(res.get("scraped_count", 0) or 0)
                    total_saved += saved_now
                    status = res.get("status", "completed")
                    last_detail = str(res.get("detail", "") or "")
                    self._push(f"  ↳ «{label}»: {saved_now} سوال ذخیره شد"
                               + (f" · {last_detail}" if last_detail else ""))
                    applog.log(f"[scrape] done topic={tid} tag={tag} "
                               f"status={status} saved={saved_now} "
                               f"detail={res.get('detail', '')}")
                    if status in {"auth_required", "limit", "blocked", "error",
                                  "interrupted", "max_minutes", "max_questions",
                                  "throttled"}:
                        break

                # Every subtopic can be complete and the topic still be short:
                # a question may belong to no subtopic page at all.  The site's
                # declared count is the only authority, and it is paid for with
                # ONE pass that skips everything already stored.
                if status == "completed":
                    for tid in sorted({t["topic_id"] for t in wanted
                                       if t["tag_id"] is not None}):
                        missing = self._missing_count(db_path, tid)
                        if missing <= 0:
                            continue
                        self._push(f"  ↳ مبحث {tid}: {missing} سوال در هیچ "
                                   "زیرمبحثی نبود — یک پاس کامل، فقط برای "
                                   "همان‌ها...")
                        res = self.harvester.scrape_topic(tid, skip_known=True)
                        saved_now = int(res.get("scraped_count", 0) or 0)
                        total_saved += saved_now
                        status = res.get("status", "completed")
                        self._push(f"  ↳ مبحث {tid}: {saved_now} سوال تازه پیدا شد"
                                   + (f" · {res.get('detail')}" if res.get("detail") else ""))
                        if status != "completed":
                            break

                self.status = status
                if status == "auth_required":
                    self._push(
                        "سایت درخواست ورود کرد (یا محدودیت ضدربات). «ورود با مرورگر» را "
                        "بزنید؛ پس از تأیید، ادامهٔ همان دانلود از صفحهٔ بعد خودکار شروع می‌شود."
                    )
                elif status == "limit" and "guest quota" in last_detail:
                    self._push("سهمیهٔ رایگان مهمان برای امروز تمام شد. با «ورود با مرورگر» "
                               "(رایگان، با کد پیامک) ادامهٔ همان مبحث خودکار از آخرین صفحهٔ "
                               "ذخیره‌شده ادامه می‌یابد؛ سهمیهٔ مهمان هم فردا از نو شروع می‌شود.")
                elif status == "limit":
                    self._push("سایت محدودیت/سقف درخواست اعلام کرد؛ بعداً ادامه دهید.")
                elif status == "throttled":
                    self._push("سایت این اتصال را موقتاً محدود کرده (۴۲۹) — علتِ "
                               "خالی‌ماندن زیرمبحث‌ها همین بود، نه اینترنت. چند "
                               "دقیقه (۱۵ تا ۳۰) صبر کن و «ادامه دانلود نیمه‌تمام» "
                               "را بزن؛ از همان صفحه ادامه می‌دهد و زیرمبحث‌های "
                               "کامل‌شده رد می‌شوند.")
                elif status == "blocked":
                    self._push("سایت این مرورگر را رد کرد (۴۰۳)؛ یک‌بار دیگر «شروع دانلود» "
                               "را بزنید — مرورگر خودکار با نسخهٔ واقعی کروم دوباره باز می‌شود.")
                elif status in {"max_pages", "max_minutes", "max_questions",
                                "interrupted"}:
                    self._push(
                        "توقف موقت (سقف صفحه/زمان یا توقف دستی) — از «ادامه دانلود "
                        "نیمه‌تمام» ادامه دهید؛ از آخرین سوال ذخیره‌شده شروع می‌شود.")
                elif multi:
                    self._push(
                        f"استخراج {len(wanted)} مبحث به پایان رسید — "
                        f"{total_saved} سوال ذخیره شد."
                    )
                else:
                    self._push(f"استخراج مبحث به پایان رسید — {total_saved} سوال ذخیره شد.")

                # Auto-update static export + printable PDF (offline, only when
                # something new was actually stored this run)
                if total_saved > 0 or self._db_grew():
                    try:
                        export_web_bundle(db_path=db_path)
                        self._push("بانک آفلاین و وب‌اپلیکیشن به‌روزرسانی شد.")
                    except Exception as ex:
                        self._push(f"خطا در به‌روزرسانی خروجی وب: {ex}")
                    # The printable book is built only when «PDF» is pressed
                    # (or `medo build-pdf`): rebuilding the whole bank's PDF
                    # after every run cost minutes for a file nobody opened.
            except Exception as ex:
                self.status = "error"
                self._push(f"خطا در استخراج: {ex}")
                applog.log(f"[scrape] traceback topics={wanted}:\n"
                           f"{traceback.format_exc()}")
            finally:
                self.is_running = False
                # Measured traffic of this run: persisted so «امروز چقدر مصرف
                # شد» survives a restart, and kept in memory for the last-run
                # line the modal shows.
                try:
                    self.run_bytes = int(
                        self.harvester.bytes_total if self.harvester else 0)
                    # A crash inside scrape_topic skips the `res` assignment,
                    # so total_saved stays 0 even though the callback has
                    # already counted the questions that *were* stored.
                    self.run_questions = int(max(total_saved,
                                               self.scraped_count))
                    conn = get_connection(db_path)
                    try:
                        record_usage(
                            conn, started_at=self.run_started_iso,
                            finished_at=datetime.now(timezone.utc).isoformat(),
                            byte_count=self.run_bytes,
                            questions=self.run_questions,
                            status=self.status, targets=wanted,
                        )
                    finally:
                        conn.close()
                    self._push(
                        f"مصرف اینترنت این اجرا: {_mb(self.run_bytes)} "
                        f"برای {self.run_questions} سوال"
                    )
                except Exception as ex:
                    applog.log(f"[usage] could not record usage: {ex}")
                self._refresh_fallback_nodes(db_path)
                # The whole request is stored: forget it only when every one
                # of its targets finished, so «ادامهٔ دانلود» never reoffers
                # a download that is actually done.
                try:
                    # Only a genuinely finished run retires its request.  A
                    # failed positioning pass must stay resumable, or the
                    # continuation (and the rest of the download) is lost.
                    if self.status == "completed" and \
                            not self._incomplete_targets(db_path, wanted):
                        conn = get_connection(db_path)
                        try:
                            clear_run_request(conn)
                        finally:
                            conn.close()
                except Exception as ex:
                    applog.log(f"[scrape] could not clear the run request: {ex}")
                # A login completed while this run was still closing: the
                # continuation is started *after* the slot is free, in its own
                # thread (this thread cannot own two Harvester browsers).
                if self._resume_pending and self.last_request and \
                        self.status in {"auth_required", "limit"}:
                    self._resume_pending = False
                    request = dict(self.last_request)
                    request["resume"] = True
                    request["start_page"] = None
                    threading.Thread(target=lambda: self.start(**request),
                                     daemon=True).start()

        # remember this request for an automatic continuation after login
        self.last_request = {
            "topic_id": wanted[0]["topic_id"],
            "start_page": start_page,
            "max_pages": max_pages,
            "db_path": db_path,
            "resume": True,
            "targets": wanted,
        }
        # Persist the request itself: the in-memory copy is lost on restart,
        # and a login confirmed after a restart still has to continue exactly
        # this download (never a guessed one).
        try:
            conn = get_connection(db_path)
            try:
                save_run_request(conn, {
                    "topic_id": wanted[0]["topic_id"],
                    "targets": wanted,
                    "max_pages": max_pages,
                    "db_path": str(db_path),
                })
            finally:
                conn.close()
        except Exception as ex:
            applog.log(f"[scrape] could not persist the run request: {ex}")

        self.thread = threading.Thread(target=_worker, daemon=True)
        self.thread.start()
        return True, ("عملیات استخراج مباحث آغاز شد." if multi
                      else "عملیات استخراج آغاز شد.")

    def resume_after_login(self) -> tuple[bool, str]:
        """Continue the interrupted download once the login is confirmed.

        The site ends an anonymous run with ``auth_required`` and keeps the
        stored ``last_page``; replaying the same request with ``resume=True``
        starts at the next unsaved page.  Only fires for a login-induced stop
        so an unrelated error is never retried in a loop.
        """
        # Login may finish while the interrupted run is still shutting its
        # browser down.  Resuming immediately collided with it (two browsers,
        # one database), so the worker is asked to do it as its last step.
        if self.is_running or (self.thread is not None and self.thread.is_alive()):
            self._resume_pending = True
            return True, "ورود تأیید شد؛ پس از پایان اجرای جاری، دانلود ادامه می‌یابد."
        # `limit` covers the guest daily allowance: the site asks for a login
        # there too, and a confirmed login lifts it — so that run resumes as
        # well, from the page after the last one stored.  An `error` / `blocked`
        # stop is not a login problem and must never be auto-retried.
        if self.status not in {"auth_required", "limit", "idle"}:
            return False, "دانلود متوقف‌شده‌ای برای ادامه وجود ندارد."
        request: dict | None = None
        if self.last_request and self.status in {"auth_required", "limit"}:
            request = dict(self.last_request)
            request["targets"] = self._incomplete_targets(
                self.db_path, request.get("targets") or [])
        else:
            request = self._resume_request(self.db_path)
        if request is None:
            return False, "دانلود متوقف‌شده‌ای برای ادامه وجود ندارد."
        request["resume"] = True
        request["start_page"] = None
        ok, msg = self.start(**request)
        if ok:
            self._push("ورود تأیید شد — ادامه دانلود از آخرین صفحه ذخیره‌شده...")
        return ok, msg

    @staticmethod
    def _incomplete_targets(db_path: Path | str,
                            targets: list[dict]) -> list[dict]:
        """Drop targets that are already fully stored.

        A multi-topic run stops at the first login wall; the topics it had
        already finished must not be re-opened one by one on the continuation.
        """
        if not targets:
            return []
        keep: list[dict] = []
        try:
            conn = get_connection(db_path)
            try:
                for t in targets:
                    st = get_target_state(conn, int(t["topic_id"]), t.get("tag_id"))
                    if (st or {}).get("status") == "completed":
                        continue
                    keep.append({
                        "topic_id": int(t["topic_id"]),
                        "tag_id": t.get("tag_id"),
                        "subtopic_title": t.get("subtopic_title"),
                    })
            finally:
                conn.close()
        except Exception as ex:
            applog.log(f"[resume] target filter failed: {ex}")
            return list(targets)
        return keep

    @classmethod
    def _resume_request(cls, db_path: Path | str) -> dict | None:
        """The stored download, with finished targets removed."""
        try:
            conn = get_connection(db_path)
            try:
                stored = load_run_request(conn)
                if stored is None:
                    return None
                targets = stored.get("targets") or []
            finally:
                conn.close()
        except Exception as ex:
            applog.log(f"[resume] could not load the stored request: {ex}")
            return None
        targets = cls._incomplete_targets(db_path, targets)
        if not targets:
            return None
        return {
            "topic_id": targets[0]["topic_id"],
            "start_page": None,
            "max_pages": stored.get("max_pages"),
            "db_path": db_path,
            "resume": True,
            "targets": targets,
        }

    @staticmethod
    def _pending_targets(db_path: Path | str,
                         statuses: tuple[str, ...]) -> list[dict]:
        """Unfinished targets read from the database (survives restarts)."""
        try:
            conn = get_connection(db_path)
            try:
                return pending_resume_targets(conn, statuses)
            finally:
                conn.close()
        except Exception as ex:
            applog.log(f"[resume] could not read pending targets: {ex}")
            return []

    def resume_pending(self, db_path: Path | str | None = None) -> tuple[bool, str]:
        """Continue whatever a previous run left unfinished.

        The manual counterpart of the post-login continuation: the Download
        modal offers it whenever the archive has an unfinished target, no
        matter whether the server was restarted or the login happened
        elsewhere (e.g. `medo login`).
        """
        if self.is_running or (self.thread is not None and self.thread.is_alive()):
            return False, "یک عملیات استخراج در حال اجراست."
        path = db_path or self.db_path
        request = self._resume_request(path)
        if request is None:
            # Deliberately no fallback to every unfinished cursor in the
            # archive: that list includes topics the user started weeks ago,
            # and one click would silently download all of them.
            return False, ("دانلود نیمه‌تمام ذخیره‌شده‌ای وجود ندارد؛ از دکمهٔ "
                           "دانلود کنار همان مبحث ادامه دهید.")
        targets = request.get("targets") or []
        ok, msg = self.start(**request)
        if ok:
            self._push(f"ادامهٔ دانلود {len(targets)} مبحث نیمه‌تمام...")
        return ok, msg

    def stop(self) -> tuple[bool, str]:
        # Repair jobs have no harvester; they watch this flag themselves.
        self.cancel_requested = True
        if self.harvester and self.is_running:
            self.harvester._interrupted = True
            self.status = "stopping"
            self._push("درخواست توقف اسکریپر ارسال شد. در حال اتمام سوال جاری...")
            return True, "درخواست توقف ارسال شد."
        if self.is_running:
            self.status = "stopping"
            self._push("درخواست توقف ارسال شد. در حال بستن کار جاری...")
            return True, "درخواست توقف ارسال شد."
        return False, "اسکریپر در حال اجرا نیست."

    # -- bank repair jobs (answers / subtopic tags) -------------------------

    def _take_slot(self, job: str, message: str,
                   db_path=DEFAULT_DB_PATH) -> tuple[bool, str]:
        """Take the single job slot; False when another job already holds it."""
        with self._job_lock:
            if self.is_running or (self.thread is not None and self.thread.is_alive()):
                return False, "عملیات دیگری در حال اجراست."
            self.is_running = True
        self.status = "running"
        self.job = job
        self.topic_id = 0
        self.current_page = 0
        self.scraped_count = 0
        self.db_path = db_path
        self.cancel_requested = False
        self.logs = []
        self.log_seq = 0
        self._push(message)
        return True, message

    def _begin_repair(self, job: str, message: str, db_path) -> tuple[bool, str]:
        """Slot for a job that needs the site: refuse when the path is broken."""
        from medo import doctor

        network_ok, verdict = doctor.quick_gate(db_path)
        if not network_ok:
            self._push(verdict)
            applog.log(f"[doctor] {job} blocked: {verdict}")
            return False, verdict
        # One consistent copy per session before anything can overwrite rows.
        from medo.backup import backup_database

        copy = backup_database(db_path)
        if copy:
            self._push(f"نسخهٔ پشتیبان بانک: {copy.name}")
        return self._take_slot(job, message, db_path)

    def _rebuild_after_repair(self, db_path) -> None:
        """Repair changes are invisible on the phone until its bundle is new."""
        try:
            export_web_bundle(db_path=db_path)
            self._push("بانک آفلاین (وب) به‌روزرسانی شد.")
        except Exception as ex:
            applog.log(f"[repair] web export failed: {ex}")
            self._push(f"خطا در به‌روزرسانی خروجی وب: {ex}")
        try:
            from medo import share
            share.write_bundle(db_path=db_path)
            self._push("فایل انتقال گوشی (medo.medo) دوباره ساخته شد.")
        except Exception as ex:
            applog.log(f"[repair] bundle failed: {ex}")
            self._push(f"خطا در ساخت بانک گوشی: {ex}")

    def start_answers_audit(self, db_path=DEFAULT_DB_PATH) -> tuple[bool, str]:
        """One resumable «ترمیم پاسخ‌ها و درس‌نامه‌ها» job."""
        from medo.answer_audit import audit_answers, repair_queue
        from medo.auth import has_storage_state

        conn = get_connection(db_path)
        try:
            pending = len(repair_queue(conn))
        finally:
            conn.close()
        if not pending:
            return False, "همهٔ سوال‌های بانک کامل‌اند؛ چیزی در صف ترمیم نیست."
        if not has_storage_state():
            return False, ("برای ترمیم باید وارد حساب شوی: اول «ورود با مرورگر» را "
                           f"بزن. ({pending} سوال در صف است.)")
        started, reason = self._begin_repair(
            "answers", f"ترمیم {pending} سوال (پاسخ و درس‌نامه)...", db_path)
        if not started:
            return False, reason

        def _worker():
            try:
                res = audit_answers(db_path, on_log=self._push,
                                    should_stop=lambda: self.cancel_requested)
                self.scraped_count = int(res["found"])
                if res.get("status") in ("limit", "daily_limit"):
                    self.status = res["status"]
                else:
                    self.status = ("interrupted" if self.cancel_requested
                                   else "completed")
                remaining = 0
                try:
                    conn = get_connection(db_path)
                    try:
                        from medo.answer_audit import repair_queue
                        remaining = len(repair_queue(conn))
                    finally:
                        conn.close()
                except Exception:
                    pass
                self._push(
                    f"پایان ترمیم: {res['found']} سوال کامل شد، "
                    f"{res['still_missing']} سوال هنوز پاسخ/درس‌نامه کم دارد"
                    + (f" ({res['skipped']} صفحه پیدا نشد)" if res["skipped"] else "")
                    + (f"؛ {res.get('texts_updated', 0)} متن به‌روز شد"
                       if res.get("texts_updated") else "")
                    + (f"؛ {res.get('kept_old', 0)} درس‌نامهٔ کامل دست‌نخورده ماند"
                       if res.get("kept_old") else "")
                    + (f"؛ {remaining} سوال در صف مانده." if remaining else ".")
                )
                if res["found"]:
                    self._rebuild_after_repair(db_path)
            except Exception as ex:
                self.status = "error"
                self._push(f"خطا در بازیابی پاسخ‌ها: {ex}")
                applog.log(f"[audit] traceback:\n{traceback.format_exc()}")
            finally:
                self.is_running = False
                try:
                    self._refresh_fallback_nodes(db_path)
                except Exception as ex:
                    applog.log(f"[audit] reconcile failed: {ex}")

        self.thread = threading.Thread(target=_worker, daemon=True)
        self.thread.start()
        return True, "بازیابی پاسخ سوالات بدون پاسخ شروع شد (با حساب واردشده)."

    def _latest_job_payload(self, kind: str = "tags") -> dict | None:
        """The durable record the UI polls; survives a page reload/restart."""
        try:
            from medo.jobs import latest_job

            conn = get_connection(self.db_path)
            try:
                return latest_job(conn, kind)
            finally:
                conn.close()
        except Exception:
            return None

    def start_tag_sync(self, db_path=DEFAULT_DB_PATH) -> tuple[bool, str]:
        """Map stored questions to their subtopics (existing backfill script)."""
        started, reason = self._begin_repair(
            "tags", "پخش سوالات در زیرمبحث‌ها شروع شد...", db_path)
        if not started:
            return False, reason

        from medo import jobs

        conn = get_connection(db_path)
        try:
            job_id = jobs.create_job(conn, "tags", current="در حال آماده‌سازی")
            jobs.add_log(conn, job_id, "job started")
        finally:
            conn.close()
        self.job_id = job_id

        def _worker():
            script = PROJECT_ROOT / "scripts" / "backfill_tags.py"
            status = "completed"
            stop_deadline = None
            try:
                proc = subprocess.Popen(
                    [sys.executable, str(script), "--all", "--db", str(db_path),
                     "--delay", "1.2", "--pause", "45",
                     "--job-id", str(job_id)],
                    cwd=str(PROJECT_ROOT), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", bufsize=1,
                )
                pending: list[str] = []

                def _reader():
                    for raw in proc.stdout or []:
                        pending.append(raw.rstrip())

                threading.Thread(target=_reader, daemon=True).start()
                while proc.poll() is None:
                    while pending:
                        line = pending.pop(0).strip()
                        if line:
                            self._push(line)
                    if self.cancel_requested:
                        if stop_deadline is None:
                            stop_deadline = time.monotonic() + 10.0
                            c = get_connection(db_path)
                            try:
                                jobs.request_stop(c, job_id)
                            finally:
                                c.close()
                            self._push("درخواست توقف ثبت شد؛ صفحهٔ جاری تمام و "
                                       "ذخیره می‌شود...")
                        elif time.monotonic() >= stop_deadline:
                            proc.terminate()
                            status = "interrupted"
                            self._push("توقف سخت پس از ۱۰ ثانیه اعمال شد.")
                            break
                    time.sleep(0.2)
                for line in pending:
                    line = line.strip()
                    if line:
                        self._push(line)
                code = proc.wait()
                conn = get_connection(db_path)
                try:
                    job = jobs.get_job(conn, job_id) or {}
                finally:
                    conn.close()
                if job.get("state") == "stopped" or self.cancel_requested:
                    status = "interrupted"
                    self._push("پخش زیرمبحث‌ها با درخواست شما متوقف شد؛ "
                               "هر صفحهٔ ذخیره‌شده مانده و اجرای بعدی از همان‌جا "
                               "ادامه می‌دهد.")
                elif job.get("state") == "error" or code not in (0,):
                    status = "error"
                    self._push("سایت این درخواست‌ها را موقتاً محدود کرد یا خطا "
                               "داد؛ هر چه ساخته شده ذخیره است. اگر «وارد حساب» "
                               "نشده‌ای اول وارد شو، کمی بعد دوباره بزن — "
                               "زیرمبحث‌های کامل‌شده رد می‌شوند.")
                else:
                    self._push("زیرمبحث‌ها به‌روزرسانی شد.")
                self._rebuild_after_repair(db_path)
            except Exception as ex:
                status = "error"
                self._push(f"خطا در به‌روزرسانی زیرمبحث‌ها: {ex}")
                applog.log(f"[tags] traceback:\n{traceback.format_exc()}")
            finally:
                self.status = status
                self.is_running = False

        self.thread = threading.Thread(target=_worker, daemon=True)
        self.thread.start()
        return True, "پخش سوالات در زیرمبحث‌ها شروع شد؛ نتیجه در همین پنجره می‌آید."

    def start_pdf_build(self, db_path=DEFAULT_DB_PATH,
                        topic_id: int | None = None) -> tuple[bool, str]:
        """Build the printable book only when asked (never after every run)."""
        # Rendering happens locally, so this job must not wait on the network.
        started, reason = self._take_slot(
            "pdf", "ساخت فایل PDF کتاب سوالات شروع شد...", db_path)
        if not started:
            return False, reason

        def _worker():
            try:
                from medo.pdf import export_pdf

                out = DIST_DIR / "medo_test_book.pdf"
                export_pdf(output_path=out, db_path=db_path,
                           topic_id=int(topic_id) if topic_id else None,
                           partial=True)
                self.status = "completed"
                self._push(f"فایل PDF ساخته شد: {out}")
            except Exception as ex:
                self.status = "error"
                self._push(f"خطا در ساخت PDF: {ex}")
                applog.log(f"[pdf] traceback:\n{traceback.format_exc()}")
            finally:
                self.is_running = False

        self.thread = threading.Thread(target=_worker, daemon=True)
        self.thread.start()
        return True, "ساخت PDF شروع شد؛ نتیجه در همین پنجره می‌آید."

    @staticmethod
    def _count_questions(db_path: Path | str = DEFAULT_DB_PATH) -> int:
        try:
            conn = get_connection(db_path)
            n = conn.execute("SELECT count(*) FROM questions").fetchone()[0]
            conn.close()
            return int(n)
        except Exception:
            return 0

    def _target_progress(self, topic_id: int, tag_id) -> tuple[int, str, int]:
        """(stored questions, cursor status, declared questions) of a target.

        Used to skip a subtopic that already finished: a re-run of «دانلود
        تک‌تک زیرمبحث‌ها» must not pay for the same pages twice — unless the
        stored count is below what the tree declares, which means the earlier
        run stopped on the site's wrong page.
        """
        try:
            conn = get_connection(self.db_path)
            try:
                if not tag_id:
                    return 0, "", 0
                n = count_node_questions(conn, int(topic_id), int(tag_id))
                state = get_target_state(conn, topic_id, tag_id) or {}
                declared = declared_questions(conn, int(topic_id), int(tag_id))
                return n, str(state.get("status") or ""), int(declared or 0)
            finally:
                conn.close()
        except Exception:
            return 0, "", 0

    def _missing_count(self, db_path: Path | str, topic_id: int) -> int:
        """Declared questions of a topic minus what the archive holds for it."""
        try:
            conn = get_connection(db_path)
            try:
                declared = declared_questions(conn, int(topic_id))
                stored = count_node_questions(conn, int(topic_id))
                return max(int(declared) - int(stored), 0)
            finally:
                conn.close()
        except Exception:
            return 0

    def _reset_truncated_target(self, topic_id: int, tag_id: int) -> None:
        """Send a truncated subtopic back to page 1 on its next run."""
        try:
            conn = get_connection(self.db_path)
            try:
                update_micro_state(conn, int(topic_id), int(tag_id),
                                   last_page=0, status="paused")
            finally:
                conn.close()
        except Exception as ex:
            applog.log(f"[scrape] could not reset truncated subtopic: {ex}")

    def _db_grew(self) -> bool:
        """True when the question count exceeded the pre-run snapshot.

        Counts the run's own database, not the global default: with a custom
        ``db_path`` the growth check used to read an unrelated file.
        """
        try:
            return self._count_questions(self.db_path) > self.stored_total_before
        except Exception:
            return False

    def _stored_pending(self) -> list[dict]:
        """Unfinished targets of the stored request, labelled for the UI.

        Only the last requested download counts: listing every unfinished
        cursor in the archive made «ادامه» promise six topics and then
        download one (or worse, surprise the user with old ones).
        """
        try:
            conn = get_connection(self.db_path)
        except Exception:
            return []
        try:
            stored = load_run_request(conn)
            if not stored:
                return []
            out: list[dict] = []
            for t in stored.get("targets") or []:
                tid = int(t.get("topic_id") or 0)
                if tid <= 0:
                    continue
                tag = t.get("tag_id")
                st = get_target_state(conn, tid, tag) or {}
                if st.get("status") == "completed":
                    continue
                from medo.db import category_title
                out.append({
                    "topic_id": tid, "tag_id": tag,
                    "label": (category_title(conn, tid, tag)
                              or t.get("subtopic_title")
                              or f"مبحث {tid}"),
                    "last_page": int(st.get("last_page") or 0),
                    "status": st.get("status") or "idle",
                })
            return out
        except Exception as ex:
            applog.log(f"[status] could not read the stored request: {ex}")
            return []
        finally:
            conn.close()

    def _usage_status(self, pending: list[dict]) -> dict:
        """Live network usage plus an estimate for what is still to download.

        The estimate is the measured average per question multiplied by the
        site-declared remaining count of the stored request's targets.  Both
        halves can be unknown (0) — the UI must show a dash, never a made-up
        number.
        """
        running = bool(self.is_running and self.harvester is not None)
        live_bytes = int(self.harvester.bytes_total) if running else 0
        live_questions = int(self.scraped_count) if running else 0

        summary = {"total_bytes": 0, "total_questions": 0, "today_bytes": 0,
                   "today_questions": 0, "avg_bytes_per_question": 0, "runs": 0}
        expected = 0
        try:
            conn = get_connection(self.db_path)
            try:
                summary = get_usage_summary(conn)
                for p in pending:
                    tid = int(p.get("topic_id") or 0)
                    if tid <= 0:
                        continue
                    tag = p.get("tag_id")
                    declared = declared_questions(conn, tid, tag)
                    if declared <= 0:
                        continue
                    stored = count_node_questions(conn, tid, tag)
                    expected += max(declared - int(stored or 0), 0)
            finally:
                conn.close()
        except Exception as ex:
            applog.log(f"[usage] could not read usage summary: {ex}")

        # Only the archive-wide average of *finished* runs.  A live average is
        # simply total-so-far ÷ questions-so-far: it fell with every question
        # (the site pre-fetches page batches), so it looked like a decreasing
        # per-question cost.  Unknown stays 0; the UI says «در حال محاسبه».
        avg = float(summary.get("avg_bytes_per_question") or 0)
        return {
            # The current/last run, measured live.
            "run_bytes": live_bytes if running else int(self.run_bytes),
            "run_questions": live_questions if running else int(self.run_questions),
            # Totals for today (the in-progress run is not in the table yet).
            "today_bytes": int(summary.get("today_bytes") or 0) + live_bytes,
            "today_questions": int(summary.get("today_questions") or 0) + live_questions,
            "avg_question_bytes": int(avg),
            "estimated_questions": int(expected),
            "estimated_bytes": int(avg * expected) if avg > 0 and expected > 0 else 0,
            "measured": bool(avg > 0),
        }

    def get_status(self) -> dict:
        """Everything the UI polls, including what can be continued.

        ``pending`` is read from the database rather than memory: the list of
        unfinished topics must survive a server restart, otherwise a finished
        login could not continue the download it was made for.
        """
        pending = self._stored_pending()
        live = (self.harvester.stats() if self.harvester is not None
                else {"requests_per_minute": 0, "delay_low": 0,
                      "delay_high": 0, "throttles_429_today": 0,
                      "questions_per_hour": 0, "eta_seconds": None,
                      "cooldown_seconds": 0, "expected_total":
                      self.expected_questions})
        if live.get("expected_total", 0) <= 0:
            live["expected_total"] = self.expected_questions
        return {
            "is_running": self.is_running,
            "status": self.status,
            "job": self.job,
            "topic_id": self.topic_id,
            "current_page": self.current_page,
            "scraped_count": self.scraped_count,
            "logs": self.logs[-25:],
            "logs_seq": self.log_seq,
            "stats": live,
            "job_progress": (self._latest_job_payload("tags")
                             if self.job in ("tags", "") else None),
            "login_in_progress": self.login_in_progress,
            "usage": self._usage_status(pending),
            "can_resume": bool(pending) and not self.is_running,
            "pending_count": len(pending),
            "pending": [
                {"topic_id": p["topic_id"], "tag_id": p["tag_id"],
                 "label": p["label"], "last_page": p["last_page"],
                 "status": p["status"]}
                for p in pending[:20]
            ],
        }


# Global background scraper instance
SCRAPER_CONTROLLER = ScraperTaskController()


class PhoneBridge:
    """Runs the LAN share server (``medo/share.py``) from the desktop app.

    The phone needs three things the practice UI cannot show: whether the
    bridge is up, the URL to type *right now* (it changes with the Wi-Fi), and
    which APK/bank is newer than the last transfer.  Everything here is
    loopback-only: MedoRequestHandler rejects foreign Host headers before any
    of these methods is reached.
    """

    def __init__(self, port: int = 8765):
        self._lock = threading.Lock()
        self._httpd = None
        self._thread: threading.Thread | None = None
        self.port = int(port)
        self.questions = 0
        self.started_at = ""
        self.building = False
        self.build_ok: bool | None = None
        self.build_log = ""
        self.build_finished_at = ""

    # ---------------------------------------------------------------- files
    @staticmethod
    def _apk_path() -> Path:
        return DIST_DIR / "medo.apk"

    @staticmethod
    def _bank_path() -> Path:
        return DIST_DIR / "medo.medo"

    @staticmethod
    def _stamp(path: Path) -> dict:
        try:
            stat = path.stat()
        except OSError:
            return {"exists": False, "bytes": 0, "mtime": ""}
        return {
            "exists": True,
            "bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        }

    # ---------------------------------------------------------------- state
    def is_running(self) -> bool:
        with self._lock:
            return self._httpd is not None

    def status(self, db_path=DEFAULT_DB_PATH) -> dict:
        from medo import share

        with self._lock:
            running = self._httpd is not None
            port = self.port
        lan = share.lan_addresses()
        suggested = f"http://{lan[0]}:{port}" if lan else f"http://127.0.0.1:{port}"
        return {
            "running": running,
            "port": port,
            "lan": lan,
            "urls": share.share_urls(port) if running else [],
            "suggested_url": suggested,
            "questions": self.questions,
            "started_at": self.started_at,
            "bank": self._stamp(self._bank_path()),
            "apk": self._stamp(self._apk_path()),
            "building": self.building,
            "build_ok": self.build_ok,
            "build_log": self.build_log[-4000:],
            "build_finished_at": self.build_finished_at,
        }

    # -------------------------------------------------------------- actions
    def start(self, db_path=DEFAULT_DB_PATH, port: int | None = None) -> tuple[bool, str]:
        from medo import share

        with self._lock:
            if self._httpd is not None:
                return True, "دسترسی وای‌فای از قبل روشن است."
            chosen = int(port if port is not None else self.port)
        try:
            httpd = share.start_share(db_path=db_path, port=chosen)
        except OSError as ex:
            return False, (f"پورت {chosen} باز نشد؛ احتمالاً برنامهٔ دیگری آن را "
                           f"گرفته است. ({ex})")
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            conn = get_connection(db_path)
            try:
                count = len(get_all_questions(conn))
            finally:
                conn.close()
        except Exception:
            count = 0
        with self._lock:
            self._httpd = httpd
            self._thread = thread
            self.port = int(httpd.server_address[1])
            self.questions = count
            self.started_at = datetime.now().strftime("%H:%M")
        applog.log(f"[phone] share started on port {self.port} ({count} questions)")
        return True, f"دسترسی وای‌فای روشن شد — {count} سوال آمادهٔ انتقال است."

    def stop(self) -> tuple[bool, str]:
        with self._lock:
            httpd, self._httpd = self._httpd, None
            self._thread = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
            applog.log("[phone] share stopped")
        return True, "دسترسی وای‌فای خاموش شد."

    def rebuild_bank(self, db_path=DEFAULT_DB_PATH) -> tuple[bool, str]:
        """Write a fresh ``dist/medo.medo`` and restart the share if it runs."""
        from medo import share

        try:
            path = share.write_bundle(db_path=db_path)
        except Exception as ex:
            return False, f"ساخت بانک ناموفق بود: {ex}"
        # The running handler holds the previous snapshot in memory; a restart
        # is the only way the phone can see the new questions.
        if self.is_running():
            self.stop()
            self.start(db_path=db_path)
        size = path.stat().st_size
        return True, f"بانک تازه ساخته شد ({size / 1024:.0f} کیلوبایت)."

    def build_apk(self) -> tuple[bool, str]:
        """Run ``android/build.sh`` in a thread and keep its log for the UI."""
        with self._lock:
            if self.building:
                return False, "ساخت APK از قبل در جریان است."
            self.building = True
            self.build_ok = None
            self.build_log = ""
        root = Path(__file__).resolve().parent.parent
        script = root / "android" / "build.sh"

        def work() -> None:
            log = ""
            ok = False
            try:
                if not script.is_file():
                    log = ("android/build.sh پیدا نشد؛ این نسخه از نرم‌افزار "
                           "فایل‌های ساخت اندروید را ندارد.\n")
                else:
                    proc = subprocess.run(
                        ["bash", str(script)], cwd=str(root),
                        capture_output=True, text=True, timeout=600,
                        encoding="utf-8", errors="replace",
                    )
                    log = (proc.stdout or "") + (proc.stderr or "")
                    ok = proc.returncode == 0 and self._apk_path().is_file()
                    if proc.returncode != 0 and not log.strip():
                        log = f"ساخت با کد {proc.returncode} متوقف شد."
            except FileNotFoundError:
                log = ("bash پیدا نشد. برنامه را از مسیر «Git Bash» باز کن یا "
                       "دستی `bash android/build.sh` را اجرا کن.\n")
            except subprocess.TimeoutExpired:
                log = "ساخت بیشتر از ۱۰ دقیقه طول کشید و متوقف شد.\n"
            except Exception as ex:  # noqa: BLE001 — the UI needs the text
                log = f"خطای ساخت APK: {ex}\n"
            with self._lock:
                self.building = False
                self.build_ok = ok
                self.build_log = log
                self.build_finished_at = datetime.now().strftime("%H:%M")
            applog.log(f"[phone] apk build ok={ok}\n{log[-2000:]}")

        threading.Thread(target=work, daemon=True).start()
        return True, "ساخت APK شروع شد؛ چند لحظه صبر کن."


PHONE_BRIDGE = PhoneBridge()


class MedoRequestHandler(SimpleHTTPRequestHandler):
    """HTTP request handler serving static files and API endpoints."""

    #: Never buffer more than this from a request body.
    MAX_BODY_BYTES = 1 << 20
    #: Host names this server answers to.  Binding to 127.0.0.1 is not enough:
    #: a DNS-rebinding page in the user's browser can make an attacker's own
    #: domain resolve here, and only a Host check stops that write reaching
    #: /api/*.
    _LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

    def __init__(self, *args, db_path=DEFAULT_DB_PATH, **kwargs):
        self.db_path = db_path
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def _host_allowed(self) -> bool:
        """True when the request was addressed to this machine by name/IP."""
        host = (self.headers.get("Host") or "").strip()
        if not host:
            return True  # HTTP/1.0 request from a local tool
        if host.startswith("["):        # [::1]:8080
            name = host.split("]", 1)[0] + "]"
        else:
            name = host.rsplit(":", 1)[0]
        return name.lower() in self._LOCAL_HOSTS

    def _drain_body(self) -> None:
        """Consume an unread request body before replying.

        Closing the socket with unread body bytes makes the OS answer with a
        TCP reset, so the client never sees our 403 — it sees a network error.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        remaining = min(max(length, 0), self.MAX_BODY_BYTES)
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)

    def _read_json(self) -> dict:
        """Request body as a JSON object; {} for anything malformed/wrong-sized."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return {}
        if length <= 0 or length > self.MAX_BODY_BYTES:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    def _asset_path(self, rel: str) -> Path | None:
        """Resolve an /assets/ URL to a file *inside* the assets directory.

        Without the containment check, ``GET /assets/../../medo/server.py``
        served project files (the path was joined and opened as-is).
        """
        try:
            root = ASSETS_DIR.resolve()
            candidate = (root / rel).resolve()
            candidate.relative_to(root)
        except (OSError, ValueError):
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _stamp_assets(html: str) -> str:
        """Append each asset's modification time to its URL in index.html.

        Browsers treat a cached CSS/JS as heuristically fresh, so after a patch
        the window kept rendering the previous interface.  A content-derived
        query string changes the URL itself whenever a file changes.
        """
        for name in ("style.css", "app.js", "manifest.json"):
            asset = WEB_DIR / name
            if not asset.is_file():
                continue
            stamp = int(asset.stat().st_mtime)
            html = re.sub(
                rf"{re.escape(name)}(\?b=\d+)?", f"{name}?b={stamp}", html
            )
        return html

    def do_GET(self):
        parsed = urlparse(self.path)

        if not self._host_allowed():
            self.send_error(403, "Forbidden host")
            return

        if parsed.path in ("/", "/index.html"):
            index = WEB_DIR / "index.html"
            if index.is_file():
                blob = self._stamp_assets(
                    index.read_text(encoding="utf-8")
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(blob)))
                self.end_headers()
                self.wfile.write(blob)
                return

        if parsed.path == "/api/questions":
            # Light rows + paging: the UI renders one question at a time and
            # fetches the full body from /api/question/<id> when it needs it.
            params = dict(
                part.split("=", 1)
                for part in (parsed.query.split("&") if parsed.query else [])
                if "=" in part
            )
            try:
                offset = max(0, int(params.get("offset") or 0))
                limit = int(params.get("limit") or 0) or None
            except ValueError:
                self._send_json({"ok": False, "message": "پارامتر صفحه نامعتبر است."})
                return
            conn = get_connection(self.db_path)
            try:
                questions = get_questions_light(conn, offset, limit)
            finally:
                conn.close()
            self._send_json(questions)
            return

        if parsed.path.startswith("/api/question/"):
            try:
                qid = int(parsed.path.rsplit("/", 1)[1])
            except ValueError:
                self._send_json({"ok": False, "message": "شناسه سوال نامعتبر است."})
                return
            conn = get_connection(self.db_path)
            try:
                question = get_question_by_id(conn, qid)
            finally:
                conn.close()
            if not question:
                self._send_json({"ok": False, "message": "سوال پیدا نشد."})
                return
            self._send_json(question)
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
            # Ask the site, not the file: an anonymous sessionid exists for
            # every guest, so file presence alone used to show a green dot.
            if has_storage_state():
                state = session_status()
            else:
                state = {"authenticated": False, "reason": "no saved session"}
            self._send_json({
                "authenticated": bool(state.get("authenticated")),
                "reason": state.get("reason", ""),
                # The window is still open: don't paint the UI red while the
                # user is typing the SMS code.
                "authorizing": bool(SCRAPER_CONTROLLER.login_in_progress),
            })
            return

        if parsed.path == "/api/phone":
            self._send_json(PHONE_BRIDGE.status(self.db_path))
            return

        if parsed.path == "/api/phone/apk":
            self._send_file(PHONE_BRIDGE._apk_path(),
                            "application/vnd.android.package-archive", "medo.apk")
            return

        if parsed.path == "/api/phone/bank":
            self._send_file(PHONE_BRIDGE._bank_path(),
                            "application/json; charset=utf-8", "medo.medo")
            return

        if parsed.path == "/api/bank/health":
            conn = get_connection(self.db_path)
            try:
                total = conn.execute("SELECT count(*) FROM questions").fetchone()[0]
                missing = conn.execute(
                    "SELECT count(*) FROM questions WHERE correct_index IS NULL"
                ).fetchone()[0]
                # A question counts as classified when ANY node of it is a
                # subtopic; the old question.tag_id column holds only one.
                untagged = count_untagged_questions(conn)
                needs_repair = count_needing_repair(conn)
                subtopics = conn.execute(
                    "SELECT count(*) FROM categories WHERE source_url LIKE '%?tag=%'"
                ).fetchone()[0]
            finally:
                conn.close()
            self._send_json({"total": int(total),
                             "missing_answers": int(missing),
                             "untagged": int(untagged),
                             "tagged": int(total) - int(untagged),
                             "needs_repair": int(needs_repair),
                             "subtopics": int(subtopics)})
            return

        if parsed.path == "/api/doctor":
            from medo import doctor
            self._send_json(doctor.run_checks(self.db_path))
            return

        if parsed.path == "/api/scrape/status":
            self._send_json(SCRAPER_CONTROLLER.get_status())
            return

        if parsed.path.startswith("/assets/"):
            # Serve local media assets (never a path outside the assets dir)
            rel_asset = parsed.path.replace("/assets/", "", 1)
            asset_file = self._asset_path(rel_asset)
            if asset_file is not None:
                self.send_response(200)
                self.send_header("Content-Type", "image/webp" if asset_file.suffix == ".webp" else "image/png")
                self.send_header("Content-Length", str(asset_file.stat().st_size))
                self.end_headers()
                self.wfile.write(asset_file.read_bytes())
                return

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if not self._host_allowed():
            self._drain_body()
            self.send_error(403, "Forbidden host")
            return

        data = self._read_json()

        if parsed.path == "/api/answer":
            # Option numbers are 1-based, so a falsy check rejected nothing —
            # but a malformed id used to raise straight out of the handler
            # thread and drop the connection.
            try:
                qid = int(data.get("question_id"))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "message": "شناسه سوال/گزینه نامعتبر است."})
                return
            # 0/null clears the answer («پاسخ مجدد»); anything else must name a
            # real option of that question, or a reload would show a stored
            # answer that no option card owns.
            raw_opt = data.get("selected_option")
            opt: int | None = None
            if raw_opt not in (None, "", 0):
                try:
                    opt = int(raw_opt)
                except (TypeError, ValueError):
                    opt = -1
            conn = get_connection(self.db_path)
            try:
                question = get_question_by_id(conn, qid)
                if question is None:
                    self._send_json({"ok": False, "message": "سوال یافت نشد."})
                    return
                if opt is not None and not 1 <= opt <= len(question.get("options") or []):
                    self._send_json({"ok": False,
                                     "message": "شناسه سوال/گزینه نامعتبر است."})
                    return
                set_user_answer(conn, qid, opt)
            finally:
                conn.close()
            self._send_json({"ok": True})
            return

        if parsed.path == "/api/bookmark":
            try:
                qid = int(data.get("question_id"))
            except (TypeError, ValueError):
                self._send_json({"ok": False, "message": "شناسه سوال نامعتبر است."})
                return
            # Explicit state when the UI sends it, toggle for older clients.
            wanted = data.get("bookmarked")
            conn = get_connection(self.db_path)
            try:
                new_state = (toggle_bookmark(conn, qid) if wanted is None
                             else set_bookmark(conn, qid, bool(wanted)))
            finally:
                conn.close()
            self._send_json({"ok": True, "bookmarked": bool(new_state)})
            return

        if parsed.path == "/api/phone/start":
            ok, msg = PHONE_BRIDGE.start(db_path=self.db_path)
            self._send_json({"ok": ok, "message": msg,
                             "phone": PHONE_BRIDGE.status(self.db_path)})
            return

        if parsed.path == "/api/phone/stop":
            ok, msg = PHONE_BRIDGE.stop()
            self._send_json({"ok": ok, "message": msg,
                             "phone": PHONE_BRIDGE.status(self.db_path)})
            return

        if parsed.path == "/api/phone/bundle":
            ok, msg = PHONE_BRIDGE.rebuild_bank(db_path=self.db_path)
            self._send_json({"ok": ok, "message": msg,
                             "phone": PHONE_BRIDGE.status(self.db_path)})
            return

        if parsed.path == "/api/phone/build-apk":
            ok, msg = PHONE_BRIDGE.build_apk()
            self._send_json({"ok": ok, "message": msg,
                             "phone": PHONE_BRIDGE.status(self.db_path)})
            return

        if parsed.path == "/api/login":
            if SCRAPER_CONTROLLER.login_in_progress:
                self._send_json({"ok": False,
                                 "message": "پنجره ورود از قبل باز است."})
                return
            SCRAPER_CONTROLLER.login_in_progress = True

            def _login_task():
                try:
                    saved = login_interactive()
                    if saved is not None:
                        # The UI polls /api/auth-status every 5 s and flips to
                        # «وارد حساب شدید» — this line says it immediately and
                        # lands in logs/medo.log for later diagnosis.
                        SCRAPER_CONTROLLER._push(
                            "ورود تأیید شد و ذخیره شد."
                        )
                        ok, msg = SCRAPER_CONTROLLER.resume_after_login()
                        if not ok:
                            SCRAPER_CONTROLLER._push(
                                "برای دریافت بقیه سوالات، دوباره «شروع دانلود» را بزنید."
                            )
                    else:
                        SCRAPER_CONTROLLER._push(
                            "لاگین تأیید نشد (پنجره بسته یا فرصت تمام شد)؛ دوباره تلاش کنید."
                        )
                except Exception as ex:
                    applog.log(f"[login] error:\n{traceback.format_exc()}")
                    SCRAPER_CONTROLLER._push(f"خطا در باز کردن لاگین: {ex}")
                finally:
                    SCRAPER_CONTROLLER.login_in_progress = False
            threading.Thread(target=_login_task, daemon=True).start()
            self._send_json({"ok": True, "message": "مرورگر برای لاگین باز شد."})
            return

        if parsed.path == "/api/scrape/start":
            # A typo in the request must answer with a message, not kill the
            # handler thread before the browser sees a response.
            try:
                topic_id = int(data.get("topic_id") or 0)
                start_page = int(data["start_page"]) if data.get("start_page") else None
                max_pages = int(data["max_pages"]) if data.get("max_pages") else None
                resume = bool(data.get("resume", False))
                tag_id = int(data["tag_id"]) if data.get("tag_id") else None
                subtopic_title = (str(data.get("subtopic_title") or "").strip()
                                  or None)
                topic_ids = None
                if isinstance(data.get("topic_ids"), list):
                    topic_ids = [int(t) for t in data["topic_ids"]
                                 if str(t).strip().isdigit()]
                targets = data.get("targets") if isinstance(data.get("targets"), list) else None
            except (TypeError, ValueError):
                self._send_json({"ok": False,
                                 "message": "پارامترهای دانلود نامعتبر است."})
                return

            ok, msg = SCRAPER_CONTROLLER.start(
                topic_id=topic_id,
                start_page=start_page,
                max_pages=max_pages,
                db_path=self.db_path,
                resume=resume,
                topic_ids=topic_ids,
                tag_id=tag_id,
                subtopic_title=subtopic_title,
                targets=targets,
            )
            self._send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/scrape/resume":
            ok, msg = SCRAPER_CONTROLLER.resume_pending(self.db_path)
            self._send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/answers/audit":
            ok, msg = SCRAPER_CONTROLLER.start_answers_audit(self.db_path)
            self._send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/tags/sync":
            ok, msg = SCRAPER_CONTROLLER.start_tag_sync(self.db_path)
            self._send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/pdf":
            # The printable book is built when asked, for the selected
            # category when there is one (one huge PDF after every run was
            # minutes of work for a file nobody opened).
            try:
                topic_id = int(data.get("topic_id") or 0) or None
            except (TypeError, ValueError):
                topic_id = None
            ok, msg = SCRAPER_CONTROLLER.start_pdf_build(self.db_path, topic_id)
            self._send_json({"ok": ok, "message": msg})
            return

        if parsed.path.startswith("/api/scrape/state"):
            params = dict(
                part.split("=", 1)
                for part in (parsed.query.split("&") if parsed.query else [])
                if "=" in part
            )
            try:
                tid = int(params.get("topic_id") or 0)
            except ValueError:
                tid = 0
            st = None
            if tid:
                conn = get_connection(self.db_path)
                st = get_scrape_state(conn, tid)
                conn.close()
            self._send_json(dict(st) if st else {"last_page": 0, "status": "idle"})
            return

        if parsed.path == "/api/scrape/stop":
            ok, msg = SCRAPER_CONTROLLER.stop()
            self._send_json({"ok": ok, "message": msg})
            return

        if parsed.path == "/api/discover-tree":
            def _discover():
                applog.log("[discover] started (site hierarchy crawl)")
                try:
                    from medo.scraper import discover_hierarchy
                    summary = discover_hierarchy(db_path=self.db_path)
                    SCRAPER_CONTROLLER._push(
                        "درخت دستهبندی گرفته شد: "
                        f"{summary.get('topics', 0)} مبحث، {summary.get('categories', 0)} دسته."
                    )
                    applog.log(f"[discover] finished: {summary}")
                    SCRAPER_CONTROLLER._refresh_fallback_nodes(self.db_path)
                except Exception as ex:
                    SCRAPER_CONTROLLER._push(f"خطا در گرفتن درخت دستهبندی: {ex}")
                    applog.log(f"[discover] failed:\n{traceback.format_exc()}")

            if SCRAPER_CONTROLLER.is_running:
                self._send_json({"ok": False, "message": "عملیات دیگری در حال اجراست."})
            else:
                SCRAPER_CONTROLLER.logs = []
                SCRAPER_CONTROLLER._push(
                    "در حال دریافت درخت دستهبندی از سایت (فقط یک‌بار لازم است)..."
                )
                threading.Thread(target=_discover, daemon=True).start()
                self._send_json({"ok": True, "message": "در حال گرفتن دستهبندیها از سایت..."})
            return

        if parsed.path == "/api/export-web":
            try:
                export_web_bundle(db_path=self.db_path)
                self._send_json({"ok": True, "message": "خروجی وب به‌روزرسانی شد."})
            except Exception as ex:
                self._send_json({"ok": False, "message": str(ex)})
            return

        self.send_error(404, "Endpoint not found")

    def end_headers(self) -> None:
        """Never let the browser cache the UI or the archive.

        Both change while the app is open (every extraction writes new
        questions, and patching the web UI replaces app.js/style.css), so a
        heuristically-cached copy made the browser show stale questions and an
        old interface.  Media under /assets/ is content-addressed (SHA-1 file
        names) and stays cacheable.
        """
        if not self.path.startswith("/assets/"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        super().end_headers()

    def _send_file(self, path: Path, ctype: str, filename: str) -> None:
        """Serve one artifact (APK or bank) with a download name."""
        file = Path(path)
        if not file.is_file():
            self._send_json({"ok": False,
                             "message": "فایل آماده نیست؛ اول آن را بساز."})
            return
        blob = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(blob)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(blob)
        except OSError:
            pass

    def _send_json(self, payload):
        blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        try:
            self.wfile.write(blob)
        except OSError:
            # The UI polls and cancels navigations: a reset socket is normal,
            # not an error worth a traceback in the log.
            pass


# Every unclassified topic costs one page load against the site's *daily
# guest allowance*.  Running that on each launch could spend the allowance
# before the user's first download even started, so the startup pass is
# rate-limited and small; `medo backfill` remains available on demand.
BACKFILL_STAMP_NAME = ".path_backfill_stamp"
BACKFILL_INTERVAL_SECONDS = 24 * 3600
BACKFILL_MAX_TOPICS = 10


def _backfill_stamp_path(db_path: Path | str) -> Path:
    return Path(db_path).parent / BACKFILL_STAMP_NAME


def _backfill_due(db_path: Path | str) -> bool:
    """True when the last startup pass is old enough to be worth repeating."""
    try:
        age = time.time() - _backfill_stamp_path(db_path).stat().st_mtime
    except OSError:
        return True
    return age >= BACKFILL_INTERVAL_SECONDS


def _startup_backfill(db_path: Path | str) -> None:
    """Best-effort: give topics without a real path one, by reading their
    breadcrumb from the site (one page each). Silently useless offline —
    the fallback grouping already covers that case."""
    # Never compete with a running extraction for the site's goodwill.
    while SCRAPER_CONTROLLER.is_running:
        time.sleep(5.0)
    time.sleep(2.0)

    if not _backfill_due(db_path):
        applog.log("[server] path backfill skipped — already attempted in the "
                   "last 24 h")
        return
    stamp = _backfill_stamp_path(db_path)

    try:
        from medo.scraper import backfill_topic_paths
        result = backfill_topic_paths(db_path=db_path, headless=True,
                                      max_topics=BACKFILL_MAX_TOPICS)
        applog.log(f"[server] path backfill result: {result}")
        try:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(f"{int(time.time())} {result}\n", encoding="utf-8")
        except OSError:
            pass
    except Exception:
        applog.log(f"[server] path backfill failed:\n{traceback.format_exc()}")


def serve(port: int = 8080, db_path: Path | str = DEFAULT_DB_PATH):
    """Start local web server."""
    # Every print from this point on (harvester progress, discover notes,
    # errors) is mirrored into logs/medo.log with timestamps.
    applog.install_tee()
    # The status endpoint (usage, pending downloads) reads the controller's
    # database, which otherwise stays on the default path until the first
    # scrape starts — a server launched with --db reported another archive.
    SCRAPER_CONTROLLER.db_path = db_path
    applog.log(f"[server] starting on 127.0.0.1:{port} (db={db_path})")
    # Offline-first grouping: questions already stored get tree nodes even
    # if the site (and hierarchy discovery) is unreachable right now.
    try:
        from medo.db import init_db

        conn = get_connection(db_path)
        try:
            # The server may be the first process to open an older archive:
            # migrate it (subtopic columns, micro cursors) before the UI asks
            # for progress, or the new tree data silently has no source.
            init_db(conn)
            result = reconcile_categories(conn)
        finally:
            conn.close()
        if result.get("created"):
            applog.log(f"[server] created {result['created']} fallback topic node(s) "
                       "(دسته‌بندی‌نشده) for stored questions")
        if result.get("paths_updated") or result.get("kinds_fixed"):
            applog.log(f"[server] category reconcile: {result}")
    except Exception:
        applog.log(f"[server] category setup failed:\n{traceback.format_exc()}")
    threading.Thread(target=_startup_backfill, args=(db_path,), daemon=True).start()

    handler_factory = lambda *args, **kwargs: MedoRequestHandler(*args, db_path=db_path, **kwargs)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_factory)
    print(f"\n[+] MedoOffline web server running at: http://127.0.0.1:{port}")
    print("[*] Press Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
        server.server_close()
