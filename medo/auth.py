"""
medo/auth.py
Authentication management for medofast.ir using Playwright storage_state.
Never prompts for, handles, or stores the user's password.

Login success is decided by the *site*: a real session is served
/dashboard/welcome, while an anonymous one is redirected to /login.  Page
text («پنل کاربری», a sessionid cookie) is NOT proof — Django hands every
guest a sessionid and shows the dashboard title to everyone.

The proof runs in Chromium, not in Python's TLS stack: this site's server
sends an extra cross-signed certificate in its chain and OpenSSL rejects the
whole handshake as "certificate has expired", while Chrome/Edge build the
path correctly (as the user's own browser does).  A raw-socket check therefore
reported "site unreachable" for a perfectly valid login.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from medo import applog
from medo.paths import DEFAULT_STORAGE_STATE

# The site's real login entry point: after a successful login it redirects
# to /dashboard/welcome (the ?next= target).
LOGIN_URL = "https://medofast.ir/login?next=/dashboard/welcome"
SITE_HOST = "medofast.ir"
DASHBOARD_URL = f"https://{SITE_HOST}/dashboard/welcome"
DASHBOARD_PATH = "/dashboard/welcome"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

_status_cache: dict[str, tuple[float, dict]] = {}
_STATUS_TTL = 90.0
# /api/auth-status is polled every few seconds; without this, two overlapping
# polls would launch two headless browsers for the same answer.
_status_lock = threading.Lock()


def has_storage_state(path: Path | str = DEFAULT_STORAGE_STATE) -> bool:
    """Check if saved session storage state file exists and is non-empty."""
    p = Path(path)
    if not p.is_file():
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return bool(data.get("cookies") or data.get("origins"))
    except Exception:
        return False


def load_cookies(path: Path | str = DEFAULT_STORAGE_STATE) -> list[dict]:
    """Cookies from a Playwright storage_state file (empty on any problem)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    return [c for c in data.get("cookies", []) if c.get("name")]


def cookie_header(cookies) -> str:
    """One `Cookie:` header value for a list of Playwright cookie dicts."""
    return "; ".join(f"{c.get('name')}={c.get('value')}"
                     for c in cookies if c.get("name"))


def parse_dashboard_navigation(status: int, url: str) -> bool:
    """True only when the dashboard itself was served.

    Chromium follows the redirect, so the guest case arrives as a 200 on
    ``/login?next=/dashboard/welcome``: the path is what separates a real
    session from a redirect shell.  Pure — unit-tested.

    >>> parse_dashboard_navigation(200, "https://medofast.ir/dashboard/welcome")
    True
    >>> parse_dashboard_navigation(200, "https://medofast.ir/login?next=/dashboard")
    False
    """
    if status != 200:
        return False
    path = urlparse(url or "").path or "/"
    if "/login" in path:
        return False
    return path.startswith("/dashboard")


def dashboard_url(url: str) -> bool:
    """True for a URL that actually IS the dashboard (query strings ignored).

    A guest lands on ``/login?next=/dashboard/welcome``: the path is
    ``/login``, so it must not count.
    """
    path = urlparse(url or "").path or "/"
    return path.startswith("/dashboard") and "/login" not in path


def _safe_url(page) -> str:
    """A tab's URL, or "" when the tab is already gone."""
    try:
        return page.url or ""
    except Exception:
        return ""


def _probe_logged_in(context, timeout_ms: int = 20000,
                     attempts: int = 2,
                     url: str = DASHBOARD_URL) -> tuple[bool, str]:
    """Open the dashboard in a fresh tab of `context`; (logged_in, final_url).

    Never raises: a navigation failure means "not confirmed", not a crash.
    Returns ``("error:<Kind>", ...)`` in the second slot when the site could
    not be reached, so the caller can tell a reset from a real redirect.
    A single retry absorbs the transient ECONNRESET this host produces when
    a discovery/backfill browser connects at the same moment.
    """
    info = ""
    for attempt in range(max(1, attempts)):
        page = None
        try:
            page = context.new_page()
            response = page.goto(url, wait_until="domcontentloaded",
                                 timeout=timeout_ms)
            status = response.status if response else 0
            info = page.url
            return parse_dashboard_navigation(status, info), info
        except Exception as ex:
            info = f"error:{type(ex).__name__}"
            applog.log(f"[auth] dashboard probe failed (attempt {attempt + 1}): "
                       f"{type(ex).__name__}: {ex}")
            time.sleep(0.8)
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
    return False, info


def _probe_storage_state(path: Path | str, timeout_ms: int = 12000) -> tuple[bool, str]:
    """Headless Chromium + saved cookies → did /dashboard/welcome open?"""
    from playwright.sync_api import sync_playwright

    from medo.browser import launch_browser, new_context

    kwargs: dict = {}
    if has_storage_state(path):
        kwargs["storage_state"] = str(path)

    with sync_playwright() as p:
        browser = launch_browser(p, headless=True)
        try:
            context = new_context(browser, **kwargs)
            return _probe_logged_in(context, timeout_ms=timeout_ms)
        finally:
            try:
                browser.close()
            except Exception:
                pass


def session_status(
    path: Path | str = DEFAULT_STORAGE_STATE,
    *,
    timeout: float = 12.0,
    max_age: float = _STATUS_TTL,
) -> dict:
    """Is the saved session really logged in?  {'authenticated', 'reason'}.

    Asks the site (in Chromium, with the saved cookies) rather than trusting
    the file's presence.  Cached briefly because the UI polls the endpoint;
    a full browser launch per poll would be wasteful.
    """
    key = str(path)
    now = time.monotonic()
    cached = _status_cache.get(key)
    if cached and now - cached[0] < max_age:
        return dict(cached[1])

    with _status_lock:
        # Another request may have finished the check while we waited.
        now = time.monotonic()
        cached = _status_cache.get(key)
        if cached and now - cached[0] < max_age:
            return dict(cached[1])

        if not has_storage_state(path):
            result = {"authenticated": False, "reason": "no saved session"}
        else:
            try:
                ok, url = _probe_storage_state(path, timeout_ms=int(timeout * 1000))
                if ok:
                    reason = "dashboard reachable"
                elif url.startswith("error:"):
                    reason = f"site unreachable ({url.split(':', 1)[1]})"
                else:
                    reason = f"redirected to {url or 'unknown'}"
                result = {"authenticated": bool(ok), "reason": reason}
            except Exception as ex:
                # Chrome missing, no network, … — never claim a session.
                result = {"authenticated": False,
                          "reason": f"browser check failed ({type(ex).__name__})"}
        _status_cache[key] = (now, result)
        if not result["authenticated"]:
            applog.log(f"[auth] session not authenticated: {result['reason']}")
        return dict(result)


def invalidate_session_cache(path: Path | str = DEFAULT_STORAGE_STATE) -> None:
    """Drop the cached verdict after saving a new session."""
    _status_cache.pop(str(path), None)


def login_interactive(
    storage_state_path: Path | str = DEFAULT_STORAGE_STATE,
    url: str = LOGIN_URL,
    timeout_seconds: float = 300.0,
    *,
    dashboard: str = DASHBOARD_URL,
    headless: bool = False,
) -> Optional[Path]:
    """
    Open a visible browser for the user to log in manually, then save the
    session cookies to storage_state_path.

    No console interaction: the app can run under pythonw (no stdin), where
    an ``input()`` call raises EOFError instantly — closing the browser
    window before the user could type anything.

    Success is verified against the site: while the window stays open, a
    second tab of the same browser context requests /dashboard/welcome every
    few seconds.  Only a real dashboard response saves the file, so a guest
    session can never overwrite a good one, and the window stays open until
    the login is genuinely complete.  (Chromium is also the only TLS stack on
    this machine that accepts the site's chain — see the module docstring.)

    Returns the path when the session was saved, None otherwise.
    """
    from playwright.sync_api import sync_playwright

    from medo.browser import launch_browser, new_context

    dest = Path(storage_state_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    applog.install_tee()  # CLI `medo login` prints land in logs/medo.log too

    with sync_playwright() as p:
        browser = launch_browser(p, headless=headless)
        try:
            # Start from the saved cookies when there are any: the site keeps
            # an anonymous session, and reusing it avoids a fresh challenge.
            ctx_kwargs: dict = {}
            if has_storage_state(dest):
                ctx_kwargs["storage_state"] = str(dest)
            context = new_context(browser, **ctx_kwargs)
            page = context.new_page()

            # A document response that actually served the dashboard is the
            # site's own proof.  Guests are redirected to /login (also a 200),
            # which the path check rejects.
            navigations: list[tuple[int, str]] = []

            def _watch(pg) -> None:
                def _on_response(resp) -> None:
                    try:
                        if resp.request.resource_type != "document":
                            return
                        target = resp.url or ""
                        if "/dashboard" in target and "/login" not in target:
                            navigations.append((resp.status, target))
                            applog.log(f"[login] dashboard response "
                                       f"{resp.status} {target}")
                    except Exception:
                        pass

                try:
                    pg.on("response", _on_response)
                except Exception:
                    pass

            for pg in context.pages:
                _watch(pg)
            context.on("page", _watch)

            # A single failed navigation (this host produces transient
            # ECONNRESETs, and the site's ads can hang the `load` event) must
            # not close the login window before the user typed anything.
            for attempt in range(3):
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    break
                except Exception as ex:
                    applog.log(f"[login] could not load {url} "
                               f"(attempt {attempt + 1}/3): "
                               f"{type(ex).__name__}: {ex}")
                    if attempt == 2:
                        print("[!] Login page did not load — check the connection, "
                              "then press reload in the browser window.")
                    else:
                        time.sleep(1.5)

            print(f"[*] Login: waiting up to {timeout_seconds:.0f} s for you to "
                  "log in on the opened browser window...")
            applog.log(f"[login] browser opened (timeout {timeout_seconds:.0f}s): {url}")

            def _cookies() -> dict[str, str]:
                try:
                    return {c["name"]: c["value"] for c in context.cookies()}
                except Exception:
                    return {}

            def _confirmed_in_page(pg) -> bool:
                """Same tab, same cookies: is the dashboard really served?

                A ``fetch`` costs nothing, never opens a tab (so it never
                steals focus from the user) and follows the guest redirect.
                """
                try:
                    res = pg.evaluate(
                        """async (u) => {
                            try {
                              const r = await fetch(u, {credentials: 'include'});
                              return {status: r.status, url: r.url};
                            } catch (e) { return {status: 0, url: ''}; }
                        }""", dashboard)
                    return parse_dashboard_navigation(
                        int(res.get("status") or 0), str(res.get("url") or ""))
                except Exception:
                    return False

            deadline = time.monotonic() + timeout_seconds
            initial = _cookies()
            reason: Optional[str] = None
            last_urls: list[str] = []
            next_check = 0.0

            # Login is proven by the site, never by page text: either the
            # dashboard document itself was served, or the session rotated
            # and the same tab can fetch the dashboard.  Both signals are
            # checked while the window stays open, so the file is written the
            # moment login succeeds instead of after a probe timeout.
            while time.monotonic() < deadline:
                if not browser.is_connected():
                    print("[+] Browser closed before login — cancelling, "
                          "existing session file kept untouched.")
                    applog.log("[login] browser closed before confirmation; "
                               "session file kept untouched")
                    return None

                # 1. The site served the dashboard to this browser.
                hit = next(((s, u) for s, u in navigations
                            if parse_dashboard_navigation(s, u)), None)
                if hit:
                    reason = hit[1]
                    break

                now = time.monotonic()
                if now >= next_check:
                    next_check = now + 1.0
                    last_urls = [_safe_url(pg) for pg in list(context.pages)]
                    last_urls = [u for u in last_urls if u]
                    pages = list(context.pages)

                    # 2. A client-side login moves the URL to /dashboard
                    #    without a new document request: ask the same tab.
                    in_dash = next((pg for pg in pages
                                    if dashboard_url(_safe_url(pg))), None)
                    if in_dash is not None:
                        if _confirmed_in_page(in_dash):
                            reason = _safe_url(in_dash)
                            break
                        applog.log("[login] dashboard URL seen but not "
                                   "served to this session yet; waiting")

                    # 3. Django rotates `sessionid` on login and the tab then
                    #    leaves /login — some accounts land on the home page,
                    #    where the URL alone says nothing.  Confirm the cookie
                    #    jar against the dashboard before saving.
                    now_cookies = _cookies()
                    rotated = (now_cookies.get("sessionid")
                               and now_cookies.get("sessionid")
                               != initial.get("sessionid"))
                    moved = any("/login" not in u for u in last_urls)
                    if rotated and moved:
                        away = next((pg for pg in pages
                                     if "/login" not in _safe_url(pg)), None)
                        if away is not None and _confirmed_in_page(away):
                            reason = _safe_url(away) or "session rotated"
                            break
                time.sleep(0.4)

            if reason:
                context.storage_state(path=str(dest))
                invalidate_session_cache(dest)
                try:
                    n_cookies = len(context.cookies())
                except Exception:
                    n_cookies = 0
                print(f"[+] Login confirmed ({reason}) — session saved to {dest}")
                applog.log(f"[login] confirmed via dashboard ({reason}); {n_cookies} "
                           f"cookie(s) saved to {dest}")
                return dest

            print("[!] Login not confirmed (timeout) — keeping the previous session file.")
            applog.log(f"[login] not confirmed within {timeout_seconds:.0f}s; "
                       f"last urls={last_urls or ['<none>']}")
            return None
        finally:
            try:
                browser.close()
            except Exception:
                pass
