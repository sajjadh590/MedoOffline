"""
medo/doctor.py
One Persian verdict on whether a download can work right now.

Every failed run used to look identical ("0 saved"): a dead Windows proxy, a
VPN pulling a fake DNS answer, TLS reset on the direct route, an expired
session, or a changed site marker.  This module checks those in order and says
which one it is, so the user fixes the right thing instead of turning the VPN
off and on again.

The checks are read-only.  ``deep`` adds the browser probe that confirms the
site still exposes ``#question-meta`` (the identity every repair depends on).
"""
from __future__ import annotations

from typing import Callable, Optional

HOST = "medofast.ir"

# Route the domain through the VPN's direct/bypass list once instead of
# switching the tunnel off for every download.
HELP_TEXT = (
    "اگر فیلترشکن روشن است، لازم نیست خاموشش کنی: در برنامهٔ VPN "
    "(v2rayN، Nekoray، Clash یا Hiddify) دامنه را در قواعد مسیر مستقیم بگذار — "
    "«domain:medofast.ir» و «geosite:ir» را به DIRECT/bypass اضافه کن. "
    "مرورگر اسکرپر خودش از --no-proxy-server استفاده می‌کند."
)


def _result(name: str, ok: bool, detail: str) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def check_network(host: str = HOST) -> list[dict]:
    """Proxy, DNS and TLS: the three ways the path to the site breaks."""
    from medo.netfix import is_fake_ip, proxy_problem, reachable, resolve_public_ip, system_ips

    checks: list[dict] = []

    problem = proxy_problem()
    checks.append(_result(
        "proxy",
        not problem,
        problem or "پروکسی ویندوز سالم است یا روشن نیست.",
    ))

    try:
        ips = system_ips(host)
    except Exception:
        ips = []
    pinned = resolve_public_ip(host) if ips else None
    if not ips and not pinned:
        checks.append(_result("dns", False,
                              f"هیچ آدرسی برای {host} پیدا نشد (DNS)."))
    elif pinned:
        checks.append(_result("dns", True,
                              f"DNS سیستم جواب جعلی VPN می‌داد؛ آدرس واقعی "
                              f"{pinned} پین می‌شود."))
    else:
        fake = all(is_fake_ip(ip) for ip in ips)
        checks.append(_result("dns", True,
                              ("آدرس DNS جعلی است ولی خود VPN ترافیک را "
                               "می‌برد." if fake else
                               f"{host} → {', '.join(ips[:2])}")))

    tls_ok = reachable(host) or bool(pinned and reachable(pinned))
    checks.append(_result(
        "tls", tls_ok,
        "اتصال TLS به سایت برقرار شد." if tls_ok else
        "TLS قطع می‌شود: مسیر VPN به medofast.ir مستقیم نیست یا فیلتر شده.",
    ))
    return checks


def check_login(storage_state=None, has_state: Optional[Callable[[], bool]] = None) -> dict:
    """Is a saved session available?  (Expiry needs the site, see deep.)"""
    from medo.auth import DEFAULT_STORAGE_STATE, has_storage_state

    state = storage_state or DEFAULT_STORAGE_STATE
    present = has_state() if has_state else has_storage_state(state)
    return _result(
        "login", bool(present),
        "نشست ذخیره‌شده هست؛ اگر سایت باز هم «ورود» خواست دوباره وارد شو."
        if present else
        "وارد حساب نشده‌ای: درس‌نامه‌ها و پاسخ‌های پرو در دسترس نیستند.",
    )


def check_identity(db_path=None) -> dict:
    """How much of the archive has a stable site question id."""
    from medo.db import DEFAULT_DB_PATH, get_connection

    path = db_path or DEFAULT_DB_PATH
    try:
        conn = get_connection(path)
        try:
            total = int(conn.execute("SELECT count(*) FROM questions").fetchone()[0] or 0)
            known = int(conn.execute(
                "SELECT count(*) FROM questions WHERE site_question_id IS NOT NULL"
            ).fetchone()[0] or 0)
        finally:
            conn.close()
    except Exception as ex:
        return _result("identity", False, f"خواندن بانک ممکن نشد: {ex}")
    if not total:
        return _result("identity", True, "بانک خالی است؛ شناسه‌ای لازم نیست.")
    return _result(
        "identity", True,
        f"{known} از {total} سوال شناسهٔ سایت دارند"
        + ("." if known == total else
           "؛ دانلود بعدی یا backfill_tags --site-ids بقیه را پر می‌کند."),
    )


def check_site_ids_live(topic: int, headless: bool = True) -> dict:
    """Open one question page and confirm the site still exposes its id."""
    try:
        from playwright.sync_api import sync_playwright

        from medo.parser import parse_question_html
        from medo.scraper import Harvester, topic_url
    except Exception as ex:  # playwright missing/broken: not a network verdict
        return _result("site id", False, f"مرورگر در دسترس نیست: {ex}")

    harvester = Harvester(headless=headless)
    url = topic_url(topic, page=1)
    try:
        with sync_playwright() as p:
            browser, page = harvester._new_page(p)
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                harvester._wait_for_question(page)
                parsed = parse_question_html(page.content(), source_url=url)
            finally:
                browser.close()
    except Exception as ex:
        return _result("site id", False, f"صفحه باز نشد: {str(ex).splitlines()[0][:120]}")
    if parsed.site_question_id:
        return _result("site id", True,
                       f"شناسهٔ سایت خوانده شد ({parsed.site_question_id}).")
    return _result("site id", False,
                   "#question-meta دیگر شناسهٔ سوال را ندارد.")


def run_checks(
    db_path=None,
    *,
    deep: bool = False,
    topic: int = 0,
    host: str = HOST,
) -> dict:
    """All checks, one verdict, one Persian message.

    ``verdict`` is ``network`` (nothing can download), ``auth`` (guest-level
    work only) or ``ready``.
    """
    checks = check_network(host)
    checks.append(check_login())
    checks.append(check_identity(db_path))
    if deep:
        checks.append(check_site_ids_live(topic or 1))

    by_name = {c["name"]: c for c in checks}
    network_ok = all(by_name[n]["ok"] for n in ("proxy", "dns", "tls") if n in by_name)
    deep_ok = by_name.get("site id", {}).get("ok", True)

    if not network_ok:
        verdict = "network"
        message = ("اختلال در مسیر شبکه: پروکسی/DNS/TLS به medofast.ir "
                   "نمی‌رسد — هیچ دانلودی الان نتیجه نمی‌دهد.")
        ok = False
    elif not deep_ok:
        verdict = "network"
        message = ("شناسهٔ سوال در سایت پیدا نشد؛ تا درست شود هر دانلود ممکن "
                   "است سوال تکراری بسازد.")
        ok = False
    elif not by_name["login"]["ok"]:
        verdict, message = "auth", "نشست منقضی شده؛ دوباره وارد شو."
        ok = True
    else:
        verdict, message = "ready", "آماده استخراج."
        ok = True

    return {
        "ok": ok,
        "verdict": verdict,
        "message": message,
        "help": HELP_TEXT,
        "checks": checks,
    }


def quick_gate(db_path=None) -> tuple[bool, str]:
    """Cheap pre-flight for a download: block only a broken network."""
    result = run_checks(db_path)
    return result["verdict"] != "network", result["message"]
