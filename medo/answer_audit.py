"""Repair stored questions from the signed-in session.

Why this exists
---------------
A scrape can store a question without its key or its lesson: the guest session
hid both, the page did not reveal them, or the run stopped before «نمایش جواب».
The app then has to tell the reader «پاسخ ثبت نشده» and cannot grade it.

This module is the one resumable repair job. It revisits only the questions
that still need the Pro session (no key, no/partial/locked lesson, or a row
stored as a guest), clicks «نمایش جواب» on the exact question, and writes the
answer, the votes and the clean lesson back. Nothing is guessed: a page whose
option texts do not match the stored question is skipped, and anything the site
still does not show is reported instead of invented.

The queue is the answer to «چرا سوال ۴ (and whatever else) خالی است»: it walks
the questions in id order and commits each one as it is repaired, so a stop,
a 429 or a daily limit costs nothing already done.
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
from medo.db import DEFAULT_DB_PATH, get_connection, questions_needing_repair


class Stopped(RuntimeError):
    """The caller asked the audit to stop (stop button / Ctrl+C)."""


def normalize_options(options) -> str:
    """Fingerprint of the option texts, insensitive to spacing/ZWNJ.

    The stem is decrypted by the site's own JS and may arrive late; the option
    texts are plain in the HTML, which is why they are the identity here.
    """
    from medo.parser import _clean_text
    from medo.text_utils import strip_invisible

    parts = []
    for option in options or []:
        text = option.get("text") if isinstance(option, dict) else getattr(option, "text", "")
        # Signatures must ignore the half-space: one side of the comparison may
        # have lost it in older rows.
        clean = strip_invisible(_clean_text(str(text or "")))
        parts.append(re.sub(r"\s+", "", clean))
    return "|".join(parts)


def _with_signature(row: dict) -> dict:
    try:
        row["signature"] = normalize_options(json.loads(row.get("options_json") or "[]"))
    except (TypeError, ValueError):
        row["signature"] = ""
    return row


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
    rows = [_with_signature(dict(row)) for row in conn.execute(sql, args).fetchall()]
    return [row for row in rows if row["signature"]]


def repair_queue(conn, topic_id: Optional[int] = None,
                 limit: Optional[int] = None) -> list[dict]:
    """Every question the signed-in session could still improve.

    Same rows the scraper's ``NEEDS_REPAIR_SQL`` counts: no key, no or partial
    lesson, or a page that was read as a guest.
    """
    rows = [_with_signature(row) for row in questions_needing_repair(conn)]
    if topic_id:
        rows = [row for row in rows if int(row.get("topic_id") or 0) == int(topic_id)]
    rows = [row for row in rows if row.get("signature") or row.get("site_question_id")]
    return rows[:int(limit)] if limit else rows


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
    require_login: bool = True,
) -> dict:
    """Re-read every question of the repair queue and keep what the site shows."""
    conn = get_connection(db_path)
    try:
        rows = repair_queue(conn, topic_id=topic_id, limit=limit)
    finally:
        conn.close()

    result = {"checked": 0, "found": 0, "still_missing": 0,
              "skipped": 0, "locked": 0, "kept_old": 0,
              "texts_updated": 0, "status": "completed"}
    if not rows:
        on_log("همهٔ سوال‌های بانک کامل‌اند؛ چیزی برای ترمیم نمانده است.")
        return result
    if require_login and not has_storage_state():
        on_log("برای ترمیم پاسخ و درس‌نامه باید وارد حساب شوی: «ورود با مرورگر» "
               "را بزن و بعد همین دکمه را تکرار کن.")
        result["status"] = "auth_required"
        return result

    # A repair overwrites stored lessons; one copy per session is cheap and
    # the only way back if a page is served in a different state.
    from medo.backup import backup_database

    copy = backup_database(db_path)
    if copy:
        on_log(f"نسخهٔ پشتیبان بانک ساخته شد: {copy.name}")

    on_log(f"{len(rows)} سوال برای ترمیم صف شده است؛ صفحه‌هایشان دوباره "
           "خوانده می‌شود (هر سوال که ذخیره شود، بلافاصله ثبت می‌شود)...")

    from medo.browser import launch_browser, new_context
    from medo.parser import parse_question_html
    from medo.scraper import LIMIT_PHRASES, Harvester, document_status

    # Images (the mind-map) are fetched and rewritten exactly like a scrape
    # does it, so a repaired lesson renders offline like any other.
    store_assets = Harvester(db_path=db_path)._store_assets

    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        kwargs = {}
        if has_storage_state():
            kwargs["storage_state"] = str(DEFAULT_STORAGE_STATE)
        context = new_context(browser, **kwargs)
        page = context.new_page()
        load_failures = 0
        try:
            for row in rows:
                if should_stop():
                    raise Stopped("stopped by user")
                tid = int(row["topic_id"] or 0)
                if tid <= 0:
                    result["skipped"] += 1
                    continue
                tag = int(row["tag_id"]) if row.get("tag_id") else None
                site_id = int(row["site_question_id"]) if row.get("site_question_id") else None
                start = int(row.get("source_page") or row.get("ordinal") or 1)
                result["checked"] += 1
                label = f"سوال {row['id']} (مبحث {tid}"
                label += f"، زیرمبحث {tag})" if tag else ")"

                matched = None
                matched_page = start
                matched_by_site_id = False
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
                                 "دوباره «ترمیم» را بزن — تا همین‌جا هر "
                                 "پاسخی که پیدا شد ذخیره شده است."
                                 if code == 429 else
                                 "سایت باز نشد (اینترنت/VPN/پروکسی را بررسی کنید؛ "
                                 "اگر سایت صفحه‌ها را موقتاً محدود کرده، کمی بعد "
                                 "دوباره بزنید): ")
                                + str(ex).splitlines()[0][:160])
                        break
                    if site_id and parsed.site_question_id == site_id:
                        matched, matched_page = parsed, page_no
                        matched_by_site_id = True
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
                    info = _persist(db_path, row, submitted, store_assets,
                                    matched_by_site_id, dry_run)
                    _count(result, info)
                    if dry_run:
                        on_log(f"{label}: پاسخ گزینهٔ {submitted.correct_index} پیدا شد (آزمایشی، ذخیره نشد).")
                    else:
                        on_log(f"{label}: پاسخ گزینهٔ {submitted.correct_index} ذخیره شد.")
                    result["found"] += 1
                elif submitted is not None and submitted.explanation_text:
                    # No key on this page, but the lesson arrived: store it.
                    info = _persist(db_path, row, submitted, store_assets,
                                    matched_by_site_id, dry_run)
                    _count(result, info)
                    result["still_missing"] += 1
                    on_log(f"{label}: درس‌نامه ذخیره شد ولی سایت پاسخی نشان نداد.")
                else:
                    result["still_missing"] += 1
                    result["locked"] += int(
                        submitted is not None
                        and str(getattr(submitted.explanation_status, "value", "")) == "locked")
                    on_log(f"{label}: سایت پاسخی نشان نداد (احتمالاً فقط با اشتراک پرو).")

                # A plan/quota wall is not a per-question failure: stop and say
                # how much of the queue is left, so the user resumes later.
                try:
                    body = page.locator("body").inner_text(timeout=8000)
                except PlaywrightError:
                    body = ""
                if any(ph in body for ph in LIMIT_PHRASES) or document_status(page) == 429:
                    result["status"] = "limit"
                    on_log("سایت سقف/محدودیت امروز را اعلام کرد — ترمیم متوقف شد؛ "
                           "هر چه تا اینجا ذخیره شد مانده است. فردا دوباره بزن.")
                    break
                time.sleep(max(0.0, delay))
        finally:
            context.close()
            browser.close()

    if result["kept_old"]:
        on_log(f"{result['kept_old']} درس‌نامهٔ کامل دست‌نخورده ماند "
               "(سایت نسخهٔ خالی/قفل برگردانده بود).")
    if result["texts_updated"]:
        on_log(f"{result['texts_updated']} متن سؤال/گزینه از صفحهٔ زنده به‌روز شد.")
    return result


def _persist(db_path, row: dict, parsed, store_assets, refresh_text: bool,
             dry_run: bool) -> dict:
    """Save one repaired page; returns what changed (empty on a dry run)."""
    if dry_run:
        return {}
    conn = get_connection(db_path)
    try:
        return _save(conn, row, parsed, store_assets, refresh_text=refresh_text)
    finally:
        conn.close()


def _count(result: dict, info: dict) -> None:
    result["kept_old"] += int(bool(info.get("explanation_kept")))
    result["texts_updated"] += int(bool(info.get("text_updated")))


def _status_value(parsed) -> str:
    """The status as a plain string (the value may be an enum or already str)."""
    status = getattr(parsed, "explanation_status", None)
    return str(getattr(status, "value", status) or "unknown")


def _keep_old_explanation(row: dict, parsed) -> bool:
    """True when the page would erase a stored FULL lesson."""
    if str(row.get("explanation_status") or "") != "full":
        return False
    old_text = str(row.get("explanation_text") or "")
    if not old_text:
        return False
    new_text = str(parsed.explanation_text or "")
    if _status_value(parsed) in ("unknown", "locked"):
        return True
    if not new_text:
        return True
    # A page that answers with a stub (typically «برای دیدن پاسخ وارد شوید»)
    # must not overwrite a long lesson with a few words.
    return len(new_text) < 0.4 * len(old_text)


def _options_text_changed(row: dict, parsed) -> bool:
    try:
        old = json.loads(row.get("options_json") or "[]")
    except (TypeError, ValueError):
        old = []
    old_pairs = [(o.get("index"), str(o.get("text") or "")) for o in old]
    new_pairs = [(o.index, str(o.text or "")) for o in parsed.options]
    return old_pairs != new_pairs


def _save(conn, row: dict, parsed, store_assets=None,
          refresh_text: bool = False) -> dict:
    """Write only the fields this pass actually recovered.

    A stored FULL lesson is never replaced by an empty, much shorter, locked or
    unknown one: a repair may add what is missing, not destroy what the account
    already paid for.
    """
    if store_assets is not None:
        try:
            store_assets(conn, parsed)
        except Exception as ex:
            # A missing mind-map must not throw away the recovered lesson.
            applog.log(f"[audit] assets for question {row['id']} failed: {ex}")

    keep_old = _keep_old_explanation(row, parsed)
    if keep_old:
        explanation_html = row.get("explanation_html")
        explanation_text = row.get("explanation_text")
        explanation_status = row.get("explanation_status")
        applog.log(f"[audit] kept old explanation: question {row['id']} "
                   f"(site returned {_status_value(parsed)})")
    else:
        explanation_html = parsed.explanation_html or row.get("explanation_html")
        explanation_text = parsed.explanation_text or row.get("explanation_text")
        explanation_status = _status_value(parsed)

    votes = parsed.vote_percent_json() if any(
        v is not None for v in (parsed.vote_percent or [])) else None

    sets = [
        "correct_index = ?",
        "answer_source = ?",
        "explanation_html = ?",
        "explanation_text = ?",
        "explanation_status = ?",
        "vote_percent_json = coalesce(?, vote_percent_json)",
        "has_images = max(has_images, ?)",
        "fetched_as = 'login'",
        "updated_at = CURRENT_TIMESTAMP",
    ]
    params = [
        parsed.correct_index,
        parsed.answer_source.value,
        explanation_html,
        explanation_text,
        explanation_status,
        votes,
        int(bool(parsed.images and explanation_html)),
    ]

    text_updated = False
    # A page matched by the site's own id is provably the same question, so its
    # current wording can replace what an older capture stored.  Identity
    # (site id, content_hash) is never recomputed here.
    if refresh_text:
        if parsed.stem_text and parsed.stem_text != (row.get("stem_text") or ""):
            sets.append("stem_text = ?")
            params.append(parsed.stem_text)
            if parsed.stem_html:
                sets.append("stem_html = ?")
                params.append(parsed.stem_html)
            text_updated = True
        if len(parsed.options) >= 2 and _options_text_changed(row, parsed):
            sets.append("options_json = ?")
            params.append(parsed.options_json())
            text_updated = True

    params.append(row["id"])
    conn.execute("UPDATE questions SET " + ", ".join(sets) + " WHERE id = ?",
                 params)
    conn.commit()
    return {"explanation_kept": keep_old, "text_updated": text_updated}
