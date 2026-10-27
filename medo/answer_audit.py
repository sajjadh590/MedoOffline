"""Find the answer for questions the archive could not grade.

Why this exists
---------------
A scrape can store a question without its key: the guest session hid the
answer, the page did not reveal it, or the run stopped before «نمایش جواب».
The app then has to tell the reader «پاسخ ثبت نشده» and cannot grade it.

This module revisits only those questions with the signed-in browser (which
may see a key the guest session did not), clicks «نمایش جواب» on the exact
question, and writes the answer back. Nothing is guessed: a page whose option
texts do not match the stored question is skipped, and an answer that the site
still does not show is reported instead of invented.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Optional

# This Playwright build exports the error as ``Error``; importing the legacy
# ``PlaywrightError`` name crashed every recovery run at the first page load.
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from medo import applog
from medo.auth import DEFAULT_STORAGE_STATE, has_storage_state
from medo.db import DEFAULT_DB_PATH, get_connection


class Stopped(RuntimeError):
    """The caller asked the audit to stop (stop button / Ctrl+C)."""


def normalize_options(options) -> str:
    """Fingerprint of the option texts, insensitive to spacing/ZWNJ.

    The stem is decrypted by the site's own JS and may arrive late; the option
    texts are plain in the HTML, which is why they are the identity here.
    """
    from medo.parser import _clean_text

    parts = []
    for option in options or []:
        text = option.get("text") if isinstance(option, dict) else getattr(option, "text", "")
        parts.append(re.sub(r"\s+", "", _clean_text(str(text or ""))))
    return "|".join(parts)


def missing_questions(conn, topic_id: Optional[int] = None,
                      limit: Optional[int] = None) -> list[dict]:
    """Stored questions without a correct option, oldest first."""
    sql = ("SELECT id, topic_id, tag_id, source_page, ordinal, stem_text, "
           "options_json, explanation_html, explanation_text "
           "FROM questions WHERE correct_index IS NULL")
    args: list = []
    if topic_id:
        sql += " AND topic_id = ?"
        args.append(int(topic_id))
    sql += " ORDER BY id"
    if limit:
        sql += " LIMIT ?"
        args.append(int(limit))
    rows = [dict(row) for row in conn.execute(sql, args).fetchall()]
    for row in rows:
        try:
            row["signature"] = normalize_options(json.loads(row.get("options_json") or "[]"))
        except (TypeError, ValueError):
            row["signature"] = ""
    return [row for row in rows if row["signature"]]


def _page_url(topic_id: int, tag_id: Optional[int], page_no: int) -> str:
    from medo.scraper import topic_url
    return topic_url(int(topic_id), page=int(page_no or 1),
                     tag_id=int(tag_id) if tag_id else None)


def audit_answers(
    db_path=DEFAULT_DB_PATH,
    topic_id: Optional[int] = None,
    limit: Optional[int] = None,
    on_log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
    dry_run: bool = False,
    delay: float = 0.7,
    scan_pages: int = 4,
) -> dict:
    """Re-read every unanswered question and store what the site reveals."""
    conn = get_connection(db_path)
    try:
        rows = missing_questions(conn, topic_id=topic_id, limit=limit)
    finally:
        conn.close()

    result = {"checked": 0, "found": 0, "still_missing": 0,
              "skipped": 0, "locked": 0}
    if not rows:
        on_log("همهٔ سوالات بانک پاسخ دارند؛ چیزی برای بازیابی نمانده است.")
        return result

    on_log(f"{len(rows)} سوال بدون پاسخ پیدا شد؛ صفحه‌هایشان دوباره خوانده می‌شود...")
    if not has_storage_state():
        on_log("هشدار: وارد حساب نشده‌اید؛ سوالاتی که سایت فقط برای اعضای پرو "
               "نشان می‌دهد بازیابی نمی‌شوند.")

    from medo.browser import launch_browser, new_context
    from medo.parser import parse_question_html
    from medo.scraper import Harvester, document_status

    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        kwargs = {}
        if has_storage_state():
            kwargs["storage_state"] = str(DEFAULT_STORAGE_STATE)
        context = new_context(browser, **kwargs)
        page = context.new_page()
        load_failures = 0
        try:
            for index, row in enumerate(rows, start=1):
                if should_stop():
                    raise Stopped("stopped by user")
                tid = int(row["topic_id"] or 0)
                if tid <= 0:
                    result["skipped"] += 1
                    continue
                tag = int(row["tag_id"]) if row.get("tag_id") else None
                start = int(row.get("source_page") or row.get("ordinal") or 1)
                result["checked"] += 1
                label = f"سوال {row['id']} (مبحث {tid}"
                label += f"، زیرمبحث {tag})" if tag else ")"

                matched = None
                matched_page = start
                for page_no in range(start, start + max(1, scan_pages) + 1):
                    try:
                        page.goto(_page_url(tid, tag, page_no),
                                  wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_selector("#label1", timeout=20000)
                        parsed = parse_question_html(page.content(),
                                                    source_url="", submitted=False,
                                                    source_page=page_no, ordinal=page_no)
                        load_failures = 0
                    except PlaywrightError as ex:
                        code = document_status(page)
                        applog.log(f"[audit] {label} page {page_no}: {ex} "
                                   f"(http={code})")
                        load_failures += 1
                        # A dead network/proxy would otherwise burn one timeout
                        # per question for the whole archive.
                        if load_failures >= 2:
                            raise RuntimeError(
                                ("سایت این اتصال را موقتاً محدود کرده (HTTP 429). "
                                 "این تقصیر اینترنت نیست؛ ۱۵ تا ۳۰ دقیقه صبر کن و "
                                 "دوباره «بازیابی پاسخ‌ها» را بزن — تا همین‌جا هر "
                                 "پاسخی که پیدا شد ذخیره شده است."
                                 if code == 429 else
                                 "سایت باز نشد (اینترنت/VPN/پروکسی را بررسی کنید؛ "
                                 "اگر سایت صفحه‌ها را موقتاً محدود کرده، کمی بعد "
                                 "دوباره بزنید): ")
                                + str(ex).splitlines()[0][:160])
                        break
                    if parsed.options and normalize_options(parsed.options) == row["signature"]:
                        matched = parsed
                        matched_page = page_no
                        break
                    time.sleep(max(0.0, delay))

                if matched is None:
                    result["skipped"] += 1
                    on_log(f"{label}: در صفحه پیدا نشد — رد شد (بعداً دوباره امتحان کنید).")
                    continue

                # The site reveals the key after an option is picked and
                # «نمایش جواب» is pressed; that is the same click the scraper
                # uses, just now with the signed-in session.
                submitted = Harvester._submit_for_answer(page, tid, matched_page)
                if submitted is not None and submitted.correct_index:
                    if dry_run:
                        on_log(f"{label}: پاسخ گزینهٔ {submitted.correct_index} پیدا شد (آزمایشی، ذخیره نشد).")
                    else:
                        update_conn = get_connection(db_path)
                        try:
                            _save(update_conn, row, submitted)
                        finally:
                            update_conn.close()
                        on_log(f"{label}: پاسخ گزینهٔ {submitted.correct_index} ذخیره شد.")
                    result["found"] += 1
                else:
                    result["still_missing"] += 1
                    result["locked"] += int(
                        submitted is not None
                        and str(getattr(submitted.explanation_status, "value", "")) == "locked")
                    on_log(f"{label}: سایت پاسخی نشان نداد (احتمالاً فقط با اشتراک پرو).")
                time.sleep(max(0.0, delay))
        finally:
            context.close()
            browser.close()

    return result


def _save(conn, row: dict, parsed) -> None:
    """Write only the fields this pass actually recovered."""
    explanation_html = parsed.explanation_html or row.get("explanation_html")
    explanation_text = parsed.explanation_text or row.get("explanation_text")
    conn.execute(
        "UPDATE questions SET correct_index = ?, answer_source = ?, "
        "explanation_html = ?, explanation_text = ?, explanation_status = ?, "
        "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (parsed.correct_index, parsed.answer_source.value,
         explanation_html, explanation_text,
         parsed.explanation_status.value, row["id"]),
    )
    conn.commit()
