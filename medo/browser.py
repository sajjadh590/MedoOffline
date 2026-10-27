"""
medo/browser.py
Playwright browser helper: launch system Chrome/Edge and give it a
fingerprint the site accepts.

medofast.ir sits behind a WAF that answers Playwright's stock headless
browser with ``403 — مرورگر شما برای این صفحه پشتیبانی نمی شود``.  Two things
give the automation away:

* the default headless user agent literally contains ``HeadlessChrome``,
* ``navigator.webdriver`` is true and the automation flags are exposed.

Sending the real browser's own Chrome version in the UA, keeping the UA and
the ``sec-ch-ua`` client hints consistent, and patching the usual JS tells is
enough to be served normal pages (verified live: 403 → 200, questions
render).  Nothing here defeats a login, a quota, or a paywall; it only makes
the browser look like the Chrome it actually is.
"""
from __future__ import annotations

import re
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Playwright

# Keep in sync with the real browser: a UA whose major version disagrees with
# the sec-ch-ua header the same Chromium sends is itself a tell.
_UA_FALLBACK_MAJOR = "141"

_UA_TEMPLATE = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36")


# Patches applied before any page script runs.  Only the classic automation
# tells are hidden; permissions and behaviour stay untouched.
_STEALTH_INIT = """
Object.defineProperty(Navigator.prototype, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {
  get: () => ['fa-IR', 'fa', 'en-US', 'en'],
});
Object.defineProperty(navigator, 'plugins', {
  get: () => [
    {name: 'PDF Viewer'}, {name: 'Chrome PDF Viewer'},
    {name: 'Chromium PDF Viewer'}, {name: 'Microsoft Edge PDF Viewer'},
    {name: 'WebKit built-in PDF'},
  ],
});
Object.defineProperty(navigator, 'mimeTypes', {
  get: () => [{type: 'application/pdf'}, {type: 'text/pdf'}],
});
window.chrome = window.chrome || {runtime: {}, loadTimes: () => {}, csi: () => {}};
const _query = window.navigator.permissions && window.navigator.permissions.query;
if (_query) {
  window.navigator.permissions.query = (p) => (
    p && p.name === 'notifications'
      ? Promise.resolve({state: Notification.permission})
      : _query.call(window.navigator.permissions, p));
}
"""


def user_agent_for(browser: Browser | None = None) -> str:
    """The real Chrome version of `browser`, dressed as a normal Windows UA."""
    major = _UA_FALLBACK_MAJOR
    try:
        m = re.match(r"(\d+)", browser.version if browser else "")
        if m:
            major = m.group(1)
    except Exception:
        pass
    return _UA_TEMPLATE.format(major=major)


def _stealth_args() -> list[str]:
    return ["--disable-blink-features=AutomationControlled",
            # Chrome's own default; Playwright turns it off in automation.
            "--no-first-run",
            "--no-default-browser-check"]


def launch_browser(p: Playwright, headless: bool = True, **kwargs: Any) -> Browser:
    """
    Launch Chromium with automatic fallback:
    1. System Google Chrome (channel='chrome')
    2. System Microsoft Edge (channel='msedge')
    3. Bundled Playwright Chromium
    """
    # Pin the site's real IP when a VPN answers with a fake one (198.18.x.x):
    # otherwise every Chromium request dies during the TLS handshake.
    from medo.netfix import host_resolver_args, proxy_problem

    # Chromium follows the Windows proxy setting, so a dead one breaks every
    # page (and the site blames the user's internet). Say it once, in the log.
    dead_proxy = proxy_problem()
    if dead_proxy:
        # Nobody is listening on it, so going direct is strictly better than
        # ERR_PROXY_CONNECTION_FAILED on every page.
        kwargs["args"] = list(kwargs.get("args") or []) + ["--no-proxy-server"]

    kwargs["args"] = list(kwargs.get("args") or []) + _stealth_args()
    if dead_proxy:
        try:
            from medo import applog
            applog.log("[net] " + dead_proxy)
        except Exception:
            pass
    resolver_args = host_resolver_args()
    if resolver_args:
        kwargs["args"] = list(kwargs["args"]) + resolver_args
    # `--enable-automation` sets navigator.webdriver and shows the infobar.
    ignore = list(kwargs.get("ignore_default_args") or [])
    if "--enable-automation" not in ignore:
        ignore.append("--enable-automation")
    kwargs["ignore_default_args"] = ignore

    channels = ["chrome", "msedge", None]
    last_err = None

    for ch in channels:
        try:
            if ch:
                return p.chromium.launch(channel=ch, headless=headless, **kwargs)
            else:
                return p.chromium.launch(headless=headless, **kwargs)
        except Exception as ex:
            last_err = ex
            continue

    if last_err:
        raise last_err
    return p.chromium.launch(headless=headless, **kwargs)


def new_context(browser: Browser, **kwargs: Any) -> BrowserContext:
    """A context that looks like the user's own Chrome (UA, locale, headers).

    Callers may override any field — e.g. ``storage_state=`` or
    ``viewport=`` — without losing the stealth defaults.
    """
    kwargs.setdefault("user_agent", user_agent_for(browser))
    kwargs.setdefault("locale", "fa-IR")
    kwargs.setdefault("timezone_id", "Asia/Tehran")
    kwargs.setdefault("viewport", {"width": 1280, "height": 900})
    kwargs.setdefault("device_scale_factor", 1)
    headers = {"Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7"}
    headers.update(kwargs.pop("extra_http_headers", None) or {})
    kwargs["extra_http_headers"] = headers
    context = browser.new_context(**kwargs)
    context.add_init_script(_STEALTH_INIT)
    return context
