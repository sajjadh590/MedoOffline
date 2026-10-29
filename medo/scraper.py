"""
medo/scraper.py
Polite, resumable, UI-driven Playwright harvester for medofast.ir.

Design (validated against the real page capture, see NOTES.md):
  * One request at a time, uniform random delay 1.5-3 s, exponential backoff
    on 429/503, immediate clean stop on 403/captcha/quota. Never parallel.
  * Pagination is driven through the real UI (button[data-nav-role="next"])
    — the underlying mechanism (XHR vs document) is irrelevant then. A
    `?page=N` fast path is attempted for resume and *verified* via the
    next-button's data-page before being trusted; otherwise we walk forward
    from page 1 by clicking.
  * Question detection uses the real parser (visibility-aware stem), never a
    hard-coded stem id (the real site has none).
  * Every parsed question is committed to SQLite (WAL) in its own transaction
    the moment it is parsed; a per-topic cursor (scrape_state) makes restarts
    land on the exact page.
  * SIGINT/SIGTERM: finish the current question, save state, print summary.
  * --max-minutes / --max-questions budgets stop the run cleanly.
  * Browser traffic is restricted to documents + XHR/fetch + JS/CSS; images,
    fonts, media, ads and trackers are aborted (needed images are downloaded
    once, deduplicated, through medo.assets).
"""
from __future__ import annotations

import hashlib
import random
import re
import signal
import time
from collections import deque
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from medo import applog
from medo.assets import download_asset
from medo.auth import DEFAULT_STORAGE_STATE, has_storage_state
from medo.db import (
    DEFAULT_DB_PATH,
    get_asset_by_url,
    get_connection,
    get_pace_state,
    get_scrape_state,
    init_db,
    save_pace_state,
    topics_needing_refill,
    update_scrape_state,
    upsert_asset,
    upsert_category,
    upsert_question,
)
from medo.html_utils import rewrite_asset_urls
from medo.models import ExplanationStatus, ParsedQuestion
from medo.paths import DEFAULT_ASSETS_DIR
from medo.parser import is_valid_stem_text, parse_question_html
from medo.text_utils import normalize_single_line

BASE_URL = "https://medofast.ir"
TOPIC_URL = BASE_URL + "/qbank/topic-id-{topic_id}"
MENU_URL = BASE_URL + "/main-menu"

# --- politeness -------------------------------------------------------------
DELAY_MIN, DELAY_MAX = 1.5, 3.0
BACKOFF_BASE, BACKOFF_MAX = 5.0, 120.0
MAX_CONSECUTIVE_ERRORS = 5
# Extra patience (1.2 s each) for the site's post-load text decryption
# ("در حال پردازش متن سوال...") after the regular parse attempts ran out.
DECRYPT_EXTRA_ATTEMPTS = 20

# --- request filtering ------------------------------------------------------
BLOCKED_URL_PATTERNS = [
    re.compile(r"yektanet", re.I),
    re.compile(r"mediaad", re.I),
    re.compile(r"google-analytics", re.I),
    re.compile(r"googletagmanager", re.I),
    re.compile(r"clarity\.ms", re.I),
    re.compile(r"hotjar", re.I),
    re.compile(r"facebook\.net|fbcdn", re.I),
    re.compile(r"\.(woff2?|ttf|otf|eot)(\?.*)?$", re.I),
]
# Everything else that is not core to rendering text content is aborted;
# needed images are fetched separately (once, deduplicated) via medo.assets.
ALLOWED_RESOURCE_TYPES = {"document", "xhr", "fetch", "script", "stylesheet"}

# --- page classification phrases -------------------------------------------
LIMIT_PHRASES = (
    "خرید اشتراک",
    "پلن پرو",
    "اشتراک ویژه",
    "نسخه پرو",
    "ارتقا حساب",
    "محدودیت روزانه",
    "سقف روزانه",
    "ظرفیت پاسخ",
    # Anonymous visitor quota: «درخواست‌های مجاز امروز شما به عنوان کاربر
    # میهمان به پایان رسیده است».  It resets daily, so it must not be
    # mistaken for a login requirement or an anti-bot block.
    "کاربر میهمان به پایان رسیده",
    "درخواست های مجاز امروز",
    "درخواستهای مجاز امروز",
)
LOGIN_PHRASES = (
    "ورود به حساب کاربری",
    "وارد شوید",
    "ورود / ثبت نام",
    # The site's SMS login page, also served as an anti-bot redirect target
    # once a burst of anonymous requests has been made.
    "کد تایید به شما پیامک",
    "سیاست حریم خصوصی",
    "در حال انتقال به سایت",
)
# The WAF's rejection page (HTTP 403).  It is not a login wall and not a
# quota: the browser itself was turned away, so retrying without a real
# Chrome fingerprint can never work.
BROWSER_BLOCK_PHRASES = (
    "مرورگر شما برای این صفحه پشتیبانی نمی شود",
    "مرورگر پشتیبانی نمی شود",
)
# The anonymous allowance ran out for today (the site redirects to
# /login?next=…&ddos_redirect=true and says «درخواست‌های مجاز امروز شما به
# عنوان کاربر میهمان به پایان رسیده»).  It resets daily and a (free) account
# lifts it, so it gets its own message instead of the generic quota one.
GUEST_QUOTA_PHRASES = (
    "کاربر میهمان به پایان رسیده",
    "درخواست های مجاز امروز",
    "درخواستهای مجاز امروز",
)
# Logged-out *topic* pages keep a "smart stats" widget visible whose prompt
# is the only readable text until the question decrypts.  Deliberately a
# separate list from LOGIN_PHRASES: menu/discovery pages must not stop just
# because they link to login; only classify_page() (topic pages, called only
# when no valid question parsed) uses these.
LOGIN_WIDGET_PHRASES = (
    "لاگین شوید",
    "لاگین سریع و بازگشت به همین سوال",
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without a browser)
# ---------------------------------------------------------------------------

def topic_url(topic_id: int, page: Optional[int] = None,
              tag_id: Optional[int] = None) -> str:
    """A topic page, optionally filtered to one subtopic and/or paginated.

    A subtopic is the site's own «مطالعه مبحثی» view: the same topic page with
    ``?tag=<id>``, which is how a sub-category is downloaded and how its
    questions become distinguishable from the rest of the topic.
    """
    url = TOPIC_URL.format(topic_id=topic_id)
    params = []
    if tag_id:
        params.append(f"tag={tag_id}")
    if page:
        params.append(f"page={page}")
    return f"{url}?{'&'.join(params)}" if params else url


# The question widget prints its own position («سوال ۲ از ۳۹»).  Unlike the
# next button's data-page it cannot go stale: the micro-topic pages render
# several hidden duplicates of that button, each with a different data-page,
# so reading it reported page 14 where the page itself said 2.
_COUNTER_RE = re.compile(r"سوال\s*([\u06f0-\u06f9\d]+)\s*از\s*([\u06f0-\u06f9\d]+)")


def read_question_position(text: str) -> tuple[Optional[int], Optional[int]]:
    """(current, total) from the page's «سوال N از M» counter.

    Returned as ``(None, None)`` when the marker is absent (login shell,
    quota notice, still-loading page) — the caller must not guess.
    """
    m = _COUNTER_RE.search(text or "")
    if not m:
        return None, None
    return int(m.group(1).translate(_FA_DIGITS)), int(m.group(2).translate(_FA_DIGITS))


def read_current_page(html: str) -> Optional[int]:
    """Current question number, preferring the page's own counter.

    Falls back to the next button's data-page (= N+1) when the counter is not
    readable.  Returns None when neither marker exists.
    """
    cur, _ = read_question_position(_strip_tags(html or ""))
    if cur is not None:
        return cur
    m = re.search(r'<button[^>]*data-nav-role="next"[^>]*data-page="(\d+)"', html)
    if not m:
        m = re.search(r'<button[^>]*data-page="(\d+)"[^>]*data-nav-role="next"', html)
    return int(m.group(1)) - 1 if m else None


def next_button_state(class_attr: str = "", style_attr: str = "", present: bool = True) -> str:
    """'ready' | 'disabled' | 'missing' — drives topic-completion detection."""
    if not present:
        return "missing"
    cls = (class_attr or "").lower()
    style = re.sub(r"\s+", " ", (style_attr or "").lower())
    if "turned-off" in cls:
        return "disabled"
    if "pointer-events: none" in style:
        return "disabled"
    if re.search(r"(?:^|;\s*)opacity:\s*0(?:\.0+)?(?:\s*;|$)", style):
        return "disabled"
    return "ready"


def classify_page(body_text: str, has_question: bool) -> str:
    """'question' | 'blocked' | 'limit' | 'auth' | 'empty' for a rendered page.

    The site shapes some notices into Arabic presentation forms
    ("در ﺣﺎل اﻧﺘﻘﺎل..."), so the text is NFKC-normalised and stripped of
    invisible characters before matching.
    """
    if has_question:
        return "question"
    text = normalize_single_line(body_text)
    if any(p in text for p in BROWSER_BLOCK_PHRASES):
        return "blocked"
    if any(p in text for p in LIMIT_PHRASES):
        return "limit"
    if any(p in text for p in LOGIN_PHRASES):
        return "auth"
    if any(p in text for p in LOGIN_WIDGET_PHRASES):
        return "auth"
    return "empty"


def is_guest_quota(body_text: str) -> bool:
    """True for the anonymous-daily-allowance notice (pure, unit-tested)."""
    text = normalize_single_line(body_text)
    return any(p in text for p in GUEST_QUOTA_PHRASES)


def document_status(page) -> Optional[int]:
    """HTTP status of the page that is open right now (None when unknown).

    Asked only after a page failed to show a question.  A rate limiter answers
    with a *friendly* HTML page (HTTP 429), which is indistinguishable from an
    empty page, so every throttled run used to be reported as a network problem
    and sent the user hunting for a VPN fault that was not there.
    """
    try:
        return int(page.context.request.get(page.url, timeout=15000).status)
    except Exception:
        return None


def fingerprint(html: str) -> str:
    """Cheap identity of the currently rendered question (options + length)."""
    opts = re.findall(r'data-encoded-text="([^"]+)"', html)
    key = "|".join(opts) + "#" + str(len(html))
    return hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()


def needs_submit(parsed: ParsedQuestion) -> bool:
    """True when the answer key is not derivable and a submit click is needed."""
    return parsed.correct_index is None and bool(parsed.options)


# --- hierarchy discovery ----------------------------------------------------
_CAT_RE = re.compile(r"/qbank/cat-id-(\d+)")
_TOPIC_RE = re.compile(r"/qbank/topic-id-(\d+)")
_KIND_BY_DEPTH = ("category", "lesson", "section")  # depth 0, 1, 2+
_FA_DIGITS = str.maketrans("\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9", "0123456789")

# A category page lists its lessons as cards pointing at /lobey/<lesson_id>;
# the card's <h2> is the Persian lesson name and its hidden progress div
# carries the real /qbank/topic-id-N to scrape.
_LESSON_CARD_RE = re.compile(
    r'<a\b[^>]*href="(/lobey/(\d+)/?)(?:\?[^"]*)?"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# Micro-topics are «شروع» buttons on /qbank/topic-id-N?tag=T (onclick only).
_TAG_TOPIC_RE = re.compile(r"/qbank/topic-id-(\d+)\?tag=(\d+)")
_MAIN_TOPIC_RE = re.compile(r"/qbank/topic-id-(\d+)(?![?\d])")
_BANK_SIZE_RE = re.compile(r"تعداد سوال در مجموعه تست مدوفست\s*:\s*([\d۰-۹,]+)")


class _LinkParser(HTMLParser):
    """Collect qbank links in document order with their visible text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self._href: Optional[str] = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a" or self._href is not None:
            return
        href = dict(attrs).get("href") or ""
        if _CAT_RE.search(href) or _TOPIC_RE.search(href):
            self._href = href
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            self.links.append({"href": self._href, "title": "".join(self._parts)})
            self._href = None
            self._parts = []


def extract_menu_links(html: str) -> list[dict]:
    """All qbank category/topic links on a page, document order, deduped.

    Pure function (unit-testable): returns [{href, title, kind, count}] where
    kind is 'topic' or a depth placeholder decided by the caller, and count is
    the first number found in the link text (Persian or ASCII digits), else 0.
    """
    from medo.text_utils import normalize_single_line as clean_text

    parser = _LinkParser()
    parser.feed(html)
    seen: set[str] = set()
    out: list[dict] = []
    for link in parser.links:
        href = link["href"]
        # Normalise to a path so ?query and #fragment never duplicate a node
        path = href.split("?")[0].split("#")[0].rstrip("/")
        if not path or "by-year" in path:
            # The site's «به تفکیک دوره و سال» route answers 502 Bad Gateway;
            # the same lessons are reachable from the category page itself.
            continue
        m_t = _TOPIC_RE.search(path)
        m_c = _CAT_RE.search(path)
        if not (m_t or m_c):
            continue
        key = path
        if key in seen:
            continue
        seen.add(key)
        title = clean_text(link["title"])
        m = re.search(r"(\d[\d,]*)", title.translate(_FA_DIGITS))
        count = int(m.group(1).replace(",", "")) if m else 0
        title = re.sub(r"[\(\[]?\s*\d[\d,]*\s*[\)\]]?\s*$", "", title).strip(" -:\u2022")
        out.append({
            "href": path,
            "site_id": int(m_t.group(1)) if m_t else int(m_c.group(1)),
            "kind": "topic" if m_t else "cat",
            "title": title,
            "count": count,
        })
    return out


# qbank landing pages ("entry points").  /main-menu itself links only to
# these; the actual cat-id/topic-id tree lives one level below them.
_ENTRY_RE = re.compile(r"^/qbank(?:-[a-z0-9-]+)?$")


def extract_entry_links(html: str) -> list[str]:
    """qbank landing pages linked from a menu page, normalised and deduped.

    Pure function (unit-testable): returns paths like ``/qbank-keshvari``.
    Absolute URLs are reduced to their path; query strings and fragments are
    dropped so the same landing page is never enqueued twice.
    """
    from urllib.parse import urlsplit

    out: list[str] = []
    seen: set[str] = set()
    for href in re.findall(r'href="([^"]+)"', html):
        path = href.split("#")[0].split("?")[0]
        if path.startswith(("http://", "https://")):
            path = urlsplit(href).path
        path = path.rstrip("/") or "/"
        if _ENTRY_RE.match(path) and path not in seen:
            seen.add(path)
            out.append(path)
    return out


# --- lesson cards / micro-topics -------------------------------------------

def _strip_tags(fragment: str) -> str:
    """Visible text of an HTML fragment, whitespace-normalised."""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", fragment)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return normalize_single_line(text)


def _int_from(text: str) -> int:
    """First number in a text fragment (Persian or ASCII digits), else 0."""
    m = re.search(r"\d[\d,]*", text.translate(_FA_DIGITS))
    return int(m.group(0).replace(",", "")) if m else 0


def extract_lesson_cards(html: str) -> list[dict]:
    """Lesson cards on a category page, in document order.

    Pure function. Each card is an <a href="/lobey/<id>"> holding the lesson
    name in <h2> and, in the real site, a hidden progress div with
    ``data-topic-id`` — the topic id the harvester actually scrapes.
    Returns [{lesson_id, href, title, topic_id}].
    """
    out: list[dict] = []
    seen: set[int] = set()
    for match in _LESSON_CARD_RE.finditer(html):
        lesson_id = int(match.group(2))
        if lesson_id in seen:
            continue
        block = match.group(3)
        heading = re.search(r"<h[12][^>]*>(.*?)</h[12]>", block, re.S)
        topic = re.search(r'data-topic-id="(\d+)"', block)
        seen.add(lesson_id)
        out.append({
            "lesson_id": lesson_id,
            "href": f"/lobey/{lesson_id}",
            "title": _strip_tags(heading.group(1)) if heading else "",
            "topic_id": int(topic.group(1)) if topic else None,
        })
    return out


def extract_lesson_page(html: str) -> dict:
    """One /lobey/<id> page → its topic id, bank size and micro-topics.

    Pure function. The page's buttons carry ``onclick="window.location.href=
    '/qbank/topic-id-3'"`` (no <a href>), which is why link-only discovery
    always found zero topics. The «حالت دوم : مطالعه مبحثی» table then lists
    the lesson's micro-topics as ``/qbank/topic-id-3?tag=<tag_id>`` with a
    per-topic question count.

    Returns {topic_id, total, micro_topics: [{tag_id, title, count}]}.
    """
    # Main topic: the first topic link WITHOUT a tag filter.
    topic_id = None
    for match in _MAIN_TOPIC_RE.finditer(html):
        tail = html[match.end():match.end() + 8]
        if not tail.lstrip().startswith("?tag="):
            topic_id = int(match.group(1))
            break

    bank = _BANK_SIZE_RE.search(_strip_tags(html))
    total = _int_from(bank.group(1)) if bank else 0

    micro: list[dict] = []
    seen: dict[int, int] = {}
    for row_match in re.finditer(r"<tr\b[^>]*>(.*?)</tr>", html, re.S | re.I):
        row = row_match.group(1)
        tag = _TAG_TOPIC_RE.search(row)
        if not tag:
            continue
        tag_id = int(tag.group(2))
        heading = re.search(r"<th\b[^>]*>(.*?)</th>", row, re.S | re.I)
        if not heading:
            cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S | re.I)
            title = _strip_tags(cells[0]) if cells else ""
        else:
            title = _strip_tags(heading.group(1))
        if not title:
            continue
        numbers = [
            _int_from(_strip_tags(cell))
            for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.S | re.I)
        ]
        count = next((n for n in numbers if n > 0), 0)
        if tag_id in seen:
            continue
        seen[tag_id] = len(micro)
        micro.append({"tag_id": tag_id, "title": title, "count": count})

    return {"topic_id": topic_id, "total": total, "micro_topics": micro}


# --- on-page topic breadcrumb ------------------------------------------------
# The question page header renders its path as a row of icon+text list items
# (font-awesome icon classes stay stable while every other class is randomised):
#   fa-books → category, fa-calendar-check → year,
#   fa-syringe → section, fa-hashtag → topic.
_PATH_ICON_RANK = {
    "books": 0,
    "calendar-check": 1,
    "calendar": 1,
    "syringe": 2,
    "hashtag": 3,
}
_PATH_ITEM_RE = re.compile(
    r'<i\b[^>]*class="([^"]*)"[^>]*>\s*</i>\s*([^<]{1,200}?)\s*</li>',
    re.IGNORECASE,
)


def extract_topic_path(html: str) -> list[str]:
    """Breadcrumb titles shown on a topic's own question page, in path order.

    Pure function — the classification source that works even when the site
    hierarchy discovery fails: the page itself prints
    «دستیاری پزشکی » 1405 » غدد » دیابت و هایپوگلایسمی» in its header.
    Parses the stable icon list items and returns the LAST strictly
    ascending run that ends with the topic marker (the header sits after any
    navigation lists). Returns [] when no topic marker with at least one
    parent segment is present.
    """
    items: list[tuple[int, str]] = []
    for cls, text in _PATH_ITEM_RE.findall(html):
        icons = [t for t in cls.split() if t.startswith("fa-")]
        if not icons:
            continue
        # Class tokens look like "fas fa-books" — strip the fa- prefix and
        # try each icon (newer Font Awesome may add style tokens too).
        rank = None
        for icon in reversed(icons):
            rank = _PATH_ICON_RANK.get(icon[3:])
            if rank is not None:
                break
        text = normalize_single_line(text)
        if rank is not None and text:
            items.append((rank, text))

    # Longest run that ends with the topic marker wins: the full header
    # (category » year » section » topic) always beats a stray pair, and an
    # unrelated later list can never replace it with a shorter one.
    best: list[str] = []
    run: list[str] = []
    prev_rank = -1
    for rank, text in items:
        if rank <= prev_rank:          # a new (ascending) run starts
            run = []
        run.append(text)
        prev_rank = rank
        if rank == 3 and len(run) >= 2 and len(run) > len(best):
            best = list(run)
    return best


def discover_hierarchy(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    headless: bool = True,
    storage_state: Optional[Path | str] = None,
    max_pages: int = 400,
    delay_range: tuple[float, float] = (DELAY_MIN, DELAY_MAX),
) -> dict:
    """Crawl the site's real tree: menus → categories → lessons → topics.

    The live site (verified 2026-10-19) needs three hops:

    * /main-menu only links the qbank landing pages (/qbank,
      /qbank-keshvari, /qbank-talifi);
    * a landing page lists the top categories as /qbank/cat-id-N/;
    * a category page lists its lessons as <a href="/lobey/L"> cards whose
      <h2> holds the Persian lesson name and whose hidden progress div carries
      ``data-topic-id`` — the real topic id the harvester scrapes;
    * a lesson page lists its micro-topics («حالت دوم : مطالعه مبحثی») as
      «شروع» buttons on /qbank/topic-id-N?tag=T.

    Link-only discovery stored the categories and then stopped: lesson links
    are /lobey/… and every topic id lives in an ``onclick`` handler, never in
    an href.  Each node is committed immediately, so a partial crawl still
    leaves a usable tree.  The site's «به تفکیک دوره و سال» route answers 502
    and is skipped; the same lessons are reachable from the category page.
    """
    from collections import deque
    from urllib.parse import urljoin

    from medo.db import prune_placeholder_nodes

    # CLI runs (no server) must land every discover note in medo.log too.
    applog.install_tee()

    conn = get_connection(db_path)
    init_db(conn)

    harvester = Harvester(db_path=db_path, storage_state=storage_state,
                          delay_range=delay_range, headless=headless)
    harvester._setup_signal_handlers()

    node_ids: dict[str, int] = {}   # source_url -> row id
    known_urls: set[str] = set()
    summary = {"nodes": 0, "categories": 0, "lessons": 0,
               "topics": 0, "micro_topics": 0}
    pages_fetched = 0
    dup_skipped = 0
    lesson_titles: dict[int, str] = {}
    lesson_topics: dict[int, Optional[int]] = {}
    lesson_positions: dict[int, int] = {}

    # queue items: (page_path, page_kind, parent_row_id, position)
    queue: deque[tuple[str, str, Optional[int], int]] = deque()
    queue.append(("/main-menu", "menu", None, 0))
    # Seed the known landings directly too: they are the only door to the
    # category tree, and discovery must not depend on one menu's markup.
    queue.append(("/qbank", "menu", None, 0))
    queue.append(("/qbank-keshvari", "menu", None, 0))
    queue.append(("/qbank-talifi", "menu", None, 0))
    visited: set[str] = set()

    with sync_playwright() as p:
        browser, page = harvester._new_page(p)

        def _load(path: str) -> bool:
            """Navigate once, retrying the WAF's transient «502 Bad Gateway»."""
            for attempt in range(3):
                try:
                    page.goto(urljoin(BASE_URL, path), wait_until="domcontentloaded",
                              timeout=45000)
                    page.wait_for_timeout(800)
                except PlaywrightError as ex:
                    if attempt == 2:
                        print(f"[!] discover: could not load {path}: {ex}")
                        return False
                    page.wait_for_timeout(2500)
                    continue
                try:
                    gateway_error = "Bad Gateway" in page.title()
                except PlaywrightError:
                    gateway_error = False
                if gateway_error:
                    if attempt == 2:
                        print(f"[!] discover: {path}: site answered 502")
                        return False
                    page.wait_for_timeout(3000)
                    continue
                return True
            return False

        try:
            while queue and pages_fetched < max_pages and not harvester._interrupted:
                path, page_kind, parent_id, position = queue.popleft()
                if path in visited:
                    dup_skipped += 1
                    if dup_skipped <= 8:
                        print(f"[i] discover: skipped already-visited {path}")
                    continue
                visited.add(path)
                if not _load(path):
                    continue
                pages_fetched += 1
                # One page's markup must never kill the whole crawl.
                try:
                    html = page.content()
                    body = page.locator("body").inner_text(timeout=8000)
                    if any(ph in body for ph in BROWSER_BLOCK_PHRASES):
                        print("[!] discover: the site rejected this browser (403 "
                              "«مرورگر پشتیبانی نمی شود») — stopping.")
                        break
                    if any(ph in body for ph in LIMIT_PHRASES):
                        print("[!] discover: quota/limit message — stopping cleanly.")
                        break
                    if "/login" in page.url or "کد تایید به شما پیامک" in body:
                        print("[!] discover: login wall — saving what was reachable so far;"
                              " run `medo login` for the rest.")
                        break

                    # Follow qbank landing pages: the category links live there,
                    # not on /main-menu itself (it links only the entry points).
                    for entry in extract_entry_links(html):
                        if entry not in visited:
                            queue.append((entry, "menu", None, 0))

                    if page_kind == "cat":
                        cards = extract_lesson_cards(html)
                        # The category's own row id becomes the lesson parent;
                        # queueing lessons with the *queue item's* parent (None)
                        # is how every topic used to end up as a root node.
                        cat_node_id = node_ids.get(path, parent_id)
                        for i, card in enumerate(cards):
                            lesson_titles[card["lesson_id"]] = card["title"]
                            lesson_topics[card["lesson_id"]] = card["topic_id"]
                            lesson_positions[card["lesson_id"]] = i
                            if card["href"] not in visited:
                                queue.append((card["href"], "lesson", cat_node_id, i))
                        print(f"[+] discover {path}: {len(cards)} lesson(s) queued")

                    elif page_kind == "lesson":
                        lesson_id = int(re.search(r"/lobey/(\d+)", path).group(1))
                        info = extract_lesson_page(html)
                        topic_id = info["topic_id"] or lesson_topics.get(lesson_id)
                        if topic_id is None:
                            # No topic id anywhere: the lesson is still a real
                            # node, keep it visible instead of dropping silently.
                            src = f"medo:lesson:{lesson_id}"
                            title = lesson_titles.get(lesson_id) or f"درس {lesson_id}"
                            if src not in known_urls:
                                upsert_category(
                                    conn, kind="lesson", title=title, source_url=src,
                                    parent_id=parent_id,
                                    position=lesson_positions.get(lesson_id, 0),
                                    question_count=info["total"],
                                )
                                known_urls.add(src)
                                summary["lessons"] += 1
                                summary["nodes"] += 1
                            print(f"[!] discover {path}: no topic id on the lesson page")
                        else:
                            title = (lesson_titles.get(lesson_id) or "").strip() or f"مبحث {topic_id}"
                            src = f"/qbank/topic-id-{topic_id}"
                            topic_row = upsert_category(
                                conn, kind="topic", title=title, source_url=src,
                                parent_id=parent_id,
                                position=lesson_positions.get(lesson_id, 0),
                                question_count=info["total"],
                            )
                            if src not in known_urls:
                                known_urls.add(src)
                                summary["topics"] += 1
                                summary["nodes"] += 1
                            node_ids[src] = topic_row
                            for j, micro in enumerate(info["micro_topics"]):
                                msrc = f"/qbank/topic-id-{topic_id}?tag={micro['tag_id']}"
                                upsert_category(
                                    conn, kind="section", title=micro["title"],
                                    source_url=msrc, parent_id=topic_row,
                                    position=j, question_count=micro["count"],
                                )
                                if msrc not in known_urls:
                                    known_urls.add(msrc)
                                    summary["micro_topics"] += 1
                                    summary["nodes"] += 1
                            print(f"[+] discover {path}: topic {topic_id} «{title}» — "
                                  f"{len(info['micro_topics'])} micro-topic(s), "
                                  f"{info['total']} question(s) in bank")

                    for i, link in enumerate(extract_menu_links(html)):
                        src = link["href"]
                        kind = "topic" if link["kind"] == "topic" else "category"
                        if src not in node_ids:
                            node_ids[src] = upsert_category(
                                conn,
                                kind=kind,
                                title=link["title"] or f"{kind} {link['site_id']}",
                                source_url=src,
                                parent_id=None,
                                position=i,
                                question_count=link["count"],
                            )
                            if src not in known_urls:
                                known_urls.add(src)
                                summary["topics" if kind == "topic" else "categories"] += 1
                                summary["nodes"] += 1
                        if kind == "category" and src not in visited:
                            queue.append((src, "cat", None, 0))
                except Exception as ex:
                    print(f"[!] discover: error while processing {path}: "
                          f"{type(ex).__name__}: {ex}")
                    harvester._polite_delay()
                    continue

                print(f"[+] discover {path}: queue {len(queue)}, "
                      f"{pages_fetched} page(s) fetched")
                harvester._polite_delay()
        finally:
            try:
                browser.close()
            except Exception:
                pass

        # Dead branches of older crawls (by-year tabs answering 502, and
        # breadcrumb-only chains whose real topic now hangs off the site tree)
        # would otherwise stay as empty rows in the UI.
        pruned = prune_placeholder_nodes(conn)
        # Questions now live under their real category in the tree; make their
        # breadcrumbs match instead of keeping year-based page paths.
        try:
            from medo.db import refresh_question_paths
            refresh_question_paths(conn)
        except Exception as ex:
            print(f"[!] discover: could not refresh question paths: {ex}")
        conn.close()

    print(f"[+] discover finished: {pages_fetched} page(s), "
          f"{dup_skipped} duplicate skip(s) — {summary['nodes']} node(s) "
          f"({summary['categories']} categories, {summary['topics']} topics, "
          f"{summary['micro_topics']} micro-topics)")
    if pruned:
        print(f"[i] discover: pruned {pruned} placeholder node(s)")
    return summary


def backfill_topic_paths(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    headless: bool = True,
    max_topics: int = 50,
) -> dict:
    """Classify topics that still lack a real path, one site page each.

    Covers questions extracted before breadcrumb classification existed or
    while the hierarchy was unreachable: loads the topic page, reads its
    on-page breadcrumb and stores the chain (``ensure_topic_path``). Never
    raises — an offline site yields ``{'classified': 0, 'error': ...}`` for
    the caller to log, and the fallback grouping keeps working meanwhile.
    """
    from medo.db import FALLBACK_ROOT_TITLE, build_topic_paths, ensure_topic_path

    applog.install_tee()

    conn = get_connection(db_path)
    init_db(conn)
    tids = sorted({
        int(r[0])
        for r in conn.execute(
            "SELECT DISTINCT topic_id FROM questions WHERE topic_id IS NOT NULL"
        ).fetchall()
    })
    paths = build_topic_paths(conn)
    missing: list[int] = []
    for tid in tids:
        path = paths.get(f"/qbank/topic-id-{tid}")
        null_rows = conn.execute(
            "SELECT count(*) FROM questions WHERE topic_id = ? "
            "AND (category_path IS NULL OR category_path = '')", (tid,)
        ).fetchone()[0]
        if (not path or path.startswith(FALLBACK_ROOT_TITLE)) or null_rows:
            missing.append(tid)
    missing = missing[:max_topics]
    result: dict = {"checked": 0, "classified": 0, "topics": missing, "error": ""}
    if not missing:
        conn.close()
        return result

    harvester = Harvester(db_path=db_path, headless=headless, fast=True)
    failures = 0
    with sync_playwright() as p:
        browser, page = harvester._new_page(p)
        try:
            for tid in missing:
                if failures >= 2:
                    result["error"] = "site unreachable (connection failed twice)"
                    break
                try:
                    page.goto(topic_url(tid), wait_until="domcontentloaded",
                              timeout=25000)
                    page.wait_for_timeout(800)
                    html = page.content()
                except Exception as ex:
                    failures += 1
                    result["error"] = f"{type(ex).__name__}: {ex}"
                    continue
                failures = 0
                result["checked"] += 1
                if "/login" in page.url:
                    result["error"] = "login required — run «ورود با مرورگر» first"
                    break
                segs = extract_topic_path(html)
                if segs:
                    ensure_topic_path(conn, tid, segs)
                    result["classified"] += 1
                else:
                    result["error"] = f"no breadcrumb found on topic {tid} page"
                harvester._polite_delay()
        finally:
            try:
                browser.close()
            except Exception:
                pass
    conn.close()
    print(f"[+] path backfill: {result['classified']}/{result['checked']} "
          f"topic(s) classified of {len(missing)} needing it"
          + (f" — {result['error']}" if result.get("error") else ""))
    return result


# ---------------------------------------------------------------------------
# Harvester
# ---------------------------------------------------------------------------

class ScraperStopped(Exception):
    """Clean, intentional stop (auth/limit/blocked/captcha/interrupt/budget)."""

    def __init__(self, status: str, message: str = ""):
        super().__init__(message or status)
        self.status = status
        self.message = message


class Harvester:
    """Polite question scraper. Sequential, resumable, interrupt-safe."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_DB_PATH,
        storage_state: Optional[Path | str] = None,
        delay_range: tuple[float, float] = (DELAY_MIN, DELAY_MAX),
        headless: bool = True,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        max_minutes: Optional[float] = None,
        max_questions: Optional[int] = None,
        assets_dir: Path | str = DEFAULT_ASSETS_DIR,
        fast: bool = False,
    ) -> None:
        import os
        self.db_path = Path(db_path)
        if storage_state is None:
            storage_state = os.environ.get("MEDO_STORAGE_STATE", DEFAULT_STORAGE_STATE)
        self.storage_state = Path(storage_state)
        # Which session read the pages of this run: the repair queue uses it to
        # know which rows a signed-in pass could still improve.
        self.fetched_as = ("login" if has_storage_state(self.storage_state)
                           else "guest")
        if fast:
            # Opt-in faster pace: still single-request, smaller delays.
            delay_range = (0.8, 1.8)
        from medo.pacing import AdaptiveDelay

        # One controller owns the pace: it widens on a 429 and settles after
        # clean pages, instead of a fixed range that is wrong in both
        # directions.  A previous run's learned range wins over the default:
        # starting fast again after a 429 just repeats the failure.
        learned = get_pace_state(self.db_path)
        self.pace = AdaptiveDelay(
            *delay_range, on_log=print,
            learned=(learned.get("low"), learned.get("high")) if learned else None)
        self.delay_min, self.delay_max = self.pace.range
        if learned:
            print(f"[pace] starting from the learned range "
                  f"{self.delay_min:.1f}-{self.delay_max:.1f}s per page")
        self.headless = headless
        self.on_progress = on_progress
        self.max_minutes = max_minutes
        self.max_questions = max_questions
        self.assets_dir = Path(assets_dir)

        self._interrupted = False
        self._deadline: Optional[float] = None
        self.run_started = time.monotonic()
        self.bytes_total = 0
        self._bytes_this_question = 0
        self._question_times: list[float] = []
        self._question_bytes: list[int] = []
        self._estimate_printed = False
        self._topic_paths: Optional[dict[str, str]] = None
        self._topics_classified: set[int] = set()
        # Live stats (UI + CLI).  Only document requests are counted: the
        # browser also fetches JS/CSS for the shell, which is not a page the
        # site throttles.
        self.requests_total = 0
        self._request_times: deque[float] = deque(maxlen=600)
        self._throttle_times: list[float] = []
        self.questions_total = 0
        self.expected_total = 0
        self.cooldown_until = 0.0
        self.cooldown_seconds = 0

    # -- signals / budgets ---------------------------------------------------

    def _setup_signal_handlers(self) -> None:
        def _handler(sig, _frame):
            print("\n[!] Graceful stop requested — finishing the current question...")
            self._interrupted = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass  # non-main thread (e.g. tests)

    @property
    def elapsed_minutes(self) -> float:
        return (time.monotonic() - self.run_started) / 60.0

    @property
    def measured_samples(self) -> int:
        return len(self._question_bytes)

    @property
    def measured_avg_bytes(self) -> float:
        """Average traffic per question in this run, start-up included.

        Deliberately the plain mean: every byte the browser fetched during the
        run is part of what the download cost, and a one-off app-shell load is
        amortised over the questions.  Fewer than three samples is reported
        as 0 (unknown) instead of a number the UI would turn into a wildly
        unstable estimate.
        """
        if len(self._question_bytes) >= 3:
            return self.bytes_total / len(self._question_bytes)
        return 0.0

    def _stop_reason(self, scraped_count: int) -> Optional[str]:
        if self._interrupted:
            return "interrupted"
        if self.max_questions is not None and scraped_count >= self.max_questions:
            return "max_questions"
        if self._deadline is not None and time.monotonic() >= self._deadline:
            return "max_minutes"
        return None

    def _polite_delay(self) -> None:
        """Uniform random delay from the adaptive range; interruptible."""
        delay = self.pace.next_delay()
        self.delay_min, self.delay_max = self.pace.range
        end = time.monotonic() + delay
        while not self._interrupted:
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.25, remaining))

    # -- browser -------------------------------------------------------------

    def _route_interceptor(self, route) -> None:
        req = route.request
        if any(p.search(req.url) for p in BLOCKED_URL_PATTERNS):
            route.abort()
            return
        if req.resource_type in ALLOWED_RESOURCE_TYPES:
            route.continue_()
        else:
            route.abort()

    @staticmethod
    def _response_bytes(resp) -> int:
        """Wire size of a response, including headers.

        A Content-Length header is not enough: the site serves the next
        question (and most assets) through HTTP/2 responses without one, so
        only the first page was counted and the live usage froze while the
        question counter kept rising.  Playwright's ``Request.sizes()``
        reports the bytes actually transferred for every resource.
        """
        try:
            sizes = resp.request.sizes()
        except Exception:
            sizes = {}
        body = int(sizes.get("responseBodySize") or 0)
        headers = int(sizes.get("responseHeadersSize") or 0)
        if body >= 0 and body + headers > 0:
            return body + headers

        # The site's own JS stores question pages in the Cache API.  A hit has
        # no network transfer (sizes reports a negative body), but its payload
        # *was* downloaded and is what the archive is made of.  Read it so the
        # live counter grows with every page instead of freezing after the
        # first batch — and so per-question averages stay meaningful.
        try:
            payload = resp.body()
            if payload:
                return len(payload)
        except Exception:
            pass
        try:
            hdr = (resp.headers or {}).get("content-length")
            if hdr and hdr.isdigit():
                return int(hdr)
        except Exception:
            pass
        return 0

    def _on_response(self, resp) -> None:
        size = self._response_bytes(resp)
        self.bytes_total += size
        self._bytes_this_question += size

    def _new_page(self, p):
        from medo.browser import launch_browser, new_context

        browser = launch_browser(p, headless=self.headless)
        kwargs: dict = {}
        if has_storage_state(self.storage_state):
            kwargs["storage_state"] = str(self.storage_state)
        # Shared context: real Chrome UA + stealth patches.  Without them the
        # WAF answers every topic page with 403 «مرورگر پشتیبانی نمی شود».
        context = new_context(browser, **kwargs)
        page = context.new_page()
        page.route("**/*", self._route_interceptor)
        page.on("response", self._on_response)
        page.on("request", self._on_document_request)
        return browser, page

    def _on_document_request(self, request) -> None:
        """Count the pages the site can throttle (documents, not assets)."""
        try:
            if request.resource_type == "document":
                self.requests_total += 1
                self._request_times.append(time.monotonic())
        except Exception:
            pass

    def _persist_pace(self) -> None:
        """Remember the range/ceiling so the next run starts carefully."""
        try:
            save_pace_state(self.db_path, *self.pace.range,
                            ceiling=self.pace.learned_ceiling or self.pace.high)
        except Exception as ex:
            applog.log(f"[pace] could not save learned range: {ex}")

    def _start_cooldown(self, wait: float) -> None:
        """Announce a 429 pause; the retry waits for the user/next run.

        No request is sent while cooling down.  The UI reads
        ``cooldown_seconds`` and shows the remaining time, so a 429 never
        looks like a frozen app or an instant retry.
        """
        self.cooldown_seconds = int(max(0, round(wait)))
        self.cooldown_until = time.time() + max(0.0, wait)
        applog.log(f"[pace] cooling down {self.cooldown_seconds}s before the "
                   "next request (no retry during the cool-down)")
        print(f"[pace] cool-down: {self.cooldown_seconds}s — retry after that")

    def stats(self) -> dict:
        """Live, cheap numbers for the UI/CLI (no site request)."""
        now = time.monotonic()
        while self._request_times and now - self._request_times[0] > 60:
            self._request_times.popleft()
        elapsed_h = max(self.elapsed_minutes / 60.0, 1e-6)
        midnights = datetime.now().replace(hour=0, minute=0, second=0,
                                           microsecond=0).timestamp()
        today_429 = sum(1 for t in self._throttle_times if t >= midnights)
        done = int(self.questions_total)
        rate = done / elapsed_h if self.elapsed_minutes > 0.05 else 0.0
        eta = None
        if self.expected_total > done and rate > 0:
            eta = int((self.expected_total - done) / rate * 3600)
        return {
            "requests_total": int(self.requests_total),
            "requests_per_minute": len(self._request_times),
            "delay_low": round(self.pace.low, 2),
            "delay_high": round(self.pace.high, 2),
            "throttles_429_today": int(today_429),
            "questions_total": done,
            "questions_per_hour": int(round(rate)),
            "expected_total": int(self.expected_total),
            "eta_seconds": eta,
            "cooldown_seconds": max(
                0, int(round(self.cooldown_until - time.time())))
            if self.cooldown_until else 0,
            "learned_ceiling": round(self.pace.learned_ceiling or 0, 2),
        }

    # -- page helpers --------------------------------------------------------

    @staticmethod
    def _wait_for_question(page: Page, timeout_ms: int = 15000) -> bool:
        """Wait until the option labels are in the DOM."""
        try:
            page.wait_for_selector("#label1", timeout=timeout_ms, state="attached")
            return True
        except PlaywrightError:
            return page.locator("#label1").count() > 0

    @staticmethod
    def _mark_hidden(page: Page) -> None:
        """Tag what the page hides, so the parser can drop the decoy blocks.

        The hiding rule lives in a per-load stylesheet that ``page.content()``
        does not include (it is injected by the site's JS), so computed style
        is the only reliable signal.  Only definite hiding counts — a zero-size
        box is skipped because a not-yet-loaded image would look the same.
        """
        try:
            page.evaluate(
                """() => {
                  const hidden = (el) => {
                    const s = getComputedStyle(el);
                    if (s.display === 'none') return true;
                    if (s.visibility === 'hidden' || s.visibility === 'collapse') return true;
                    if (parseFloat(s.opacity) === 0) return true;
                    if (s.position === 'absolute' || s.position === 'fixed') {
                      if (parseFloat(s.left) <= -9999) return true;
                    }
                    return false;
                  };
                  document.querySelectorAll('#qtxt *, #block-b *').forEach((el) => {
                    if (hidden(el)) el.setAttribute('data-medo-hidden', '1');
                  });
                }"""
            )
        except PlaywrightError:
            pass  # a page mid-navigation: the next parse will retry

    @staticmethod
    def _wait_for_reveal(page: Page, timeout_ms: int = 8000) -> bool:
        """Wait until #block-b is visible with real text (or a mindmap image).

        The old fixed 700-800 ms wait captured half-built lessons on a slow
        page; innerText ignores display:none, so decoy blocks do not count.
        """
        deadline = time.monotonic() + timeout_ms / 1000
        while time.monotonic() < deadline:
            try:
                state = page.evaluate(
                    """() => {
                      const b = document.querySelector('#block-b');
                      if (!b) return null;
                      const s = getComputedStyle(b);
                      return {
                        visible: s.display !== 'none' && s.visibility !== 'hidden'
                                 && parseFloat(s.opacity) !== 0,
                        text: (b.innerText || '').trim(),
                        img: !!b.querySelector('img[src]'),
                      };
                    }"""
                )
            except PlaywrightError:
                return False
            if state and state.get("visible") and (
                len(state.get("text") or "") > 0 or state.get("img")
            ):
                return True
            page.wait_for_timeout(350)
        return False

    @staticmethod
    def _parse_visible(page: Page, topic_id: int, page_no: int,
                       submitted: bool = False) -> ParsedQuestion:
        Harvester._mark_hidden(page)
        html = page.content()
        return parse_question_html(
            html,
            source_url=topic_url(topic_id),
            source_page=page_no,
            ordinal=page_no,
            submitted=submitted,
        )

    @classmethod
    def _parse_with_retry(cls, page: Page, topic_id: int, page_no: int,
                          max_attempts: int = 8) -> Optional[ParsedQuestion]:
        """Parse once the real question is on screen.

        Both halves have to hold:

        * the option labels must exist — without them the page is a login
          wall, quota notice or the site's redirect shell;
        * the stem must pass ``is_valid_stem_text`` — until the site's JS
          decrypts the question, the only readable paragraphs belong to the
          login/statistics widget.

        Storing anything less is how junk "questions" reached the archive, so
        this waits out the full patience before giving up.
        """
        for attempt in range(max_attempts):
            if page.locator("#label1").count() > 0:
                q = cls._parse_visible(page, topic_id, page_no)
                if q.stem_text and is_valid_stem_text(q.stem_text):
                    return q
            page.wait_for_timeout(1200)

        # The site decrypts the question text *after* the shell is up
        # ("در حال پردازش متن سوال...").  Give that slow path a second,
        # longer window before giving up — otherwise a page that was merely
        # slow to decrypt ended the whole run as "completed, 0 questions".
        for _ in range(DECRYPT_EXTRA_ATTEMPTS):
            if page.locator("#label1").count() > 0:
                q = cls._parse_visible(page, topic_id, page_no)
                if q.stem_text and is_valid_stem_text(q.stem_text):
                    return q
            try:
                body = page.locator("body").inner_text(timeout=3000)
            except PlaywrightError:
                break
            if "در حال پردازش" not in normalize_single_line(body):
                break  # no longer decrypting — a real wall/empty page
            page.wait_for_timeout(1200)
        return None

    @classmethod
    def _submit_for_answer(cls, page: Page, topic_id: int, page_no: int) -> Optional[ParsedQuestion]:
        """Fallback: choose option 1 and read the revealed colours."""
        try:
            page.locator("#label1").click(timeout=4000)
            btn = page.locator("#show-b-btn")
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=4000)
                cls._wait_for_reveal(page)
            return cls._parse_visible(page, topic_id, page_no, submitted=True)
        except PlaywrightError:
            return None

    @classmethod
    def _reveal_explanation(cls, page: Page, topic_id: int, page_no: int,
                            q: ParsedQuestion) -> ParsedQuestion:
        """Reveal and capture the explanation (text + figure) when available.

        The site shows #block-b — the explanation, often with an inline image
        or a mindmap in #image-container — only after "نمایش جواب" is clicked.
        Until now that click happened only when the answer marker itself was
        missing, so stored questions kept the key but lost the explanation.
        Only called when the stored explanation is incomplete; one local click,
        no extra network traffic.
        """
        try:
            btn = page.locator("#show-b-btn")
            if btn.count() == 0 or not btn.is_visible():
                return q
            btn.click(timeout=4000)
            cls._wait_for_reveal(page)
            q2 = cls._parse_visible(page, topic_id, page_no, submitted=True)
        except PlaywrightError:
            return q
        if q2 is None or not q2.stem_text:
            return q
        if q2.explanation_text and (
            not q.explanation_text
            or len(q2.explanation_text) > len(q.explanation_text)
        ):
            return q2
        return q

    @staticmethod
    def _next_state(page: Page) -> str:
        """'ready' | 'disabled' | 'missing' for the live next button.

        The widget renders several duplicate next buttons (one live, the rest
        stale clones).  The *last* one is the live one — `.first` was a clone
        whose data-page and click target were wrong, which is what made a
        resumed subtopic stop at «topic shorter than resume target».
        """
        nxt = page.locator('button[data-nav-role="next"]')
        count = nxt.count()
        if count == 0:
            return "missing"
        # The counter is the truth: at the last question there is nowhere to go
        # even if a stale clone still looks enabled.
        try:
            cur, total = read_question_position(
                page.locator("body").inner_text(timeout=5000))
            if cur is not None and total is not None and cur >= total:
                return "disabled"
        except PlaywrightError:
            pass
        try:
            live = nxt.nth(count - 1)
            return next_button_state(
                class_attr=live.get_attribute("class") or "",
                style_attr=live.get_attribute("style") or "",
                present=True,
            )
        except PlaywrightError:
            return "missing"

    @classmethod
    def _click_next(cls, page: Page) -> bool:
        """Click the live next button and wait for a different question."""
        return cls._click_nav(page, "next")

    @classmethod
    def _click_nav(cls, page: Page, role: str) -> bool:
        """Click the live nav button (`next`/`prev`) and wait for a new question.

        Tries the candidate buttons from last to first: the visible live one
        is last, and a stale clone either intercepts the click or does
        nothing, which used to end a run as if the topic were finished.
        """
        btns = page.locator(f'button[data-nav-role="{role}"]')
        count = btns.count()
        if count == 0:
            return False
        for idx in range(count - 1, -1, -1):
            try:
                before = fingerprint(page.content())
            except PlaywrightError:
                return False
            try:
                btns.nth(idx).click(timeout=3000)
            except PlaywrightError:
                continue  # intercepted/covered clone — try the next one
            deadline = time.monotonic() + 6.0
            while time.monotonic() < deadline:
                page.wait_for_timeout(400)
                try:
                    if fingerprint(page.content()) != before:
                        page.wait_for_timeout(600)  # let the reveal-JS settle
                        return True
                except PlaywrightError:
                    return False
        return False

    def _position_at(self, page: Page, topic_id: int, target: int,
                     tag_id: Optional[int] = None) -> Optional[int]:
        """Land on `target` (1-based) and return the page actually shown.

        The site tracks the reader's position in the *session*, so neither
        ``?page=N`` nor a fresh load is guaranteed to start at page 1.  The
        «سوال N از M» counter is therefore the only trusted marker: it says
        where we are, whether the topic is shorter than the target, and how
        many clicks are left.  Returning the actual page (not the requested
        one) keeps stored page numbers truthful.
        """
        def position() -> tuple[Optional[int], Optional[int]]:
            try:
                return read_question_position(
                    page.locator("body").inner_text(timeout=8000))
            except PlaywrightError:
                return None, None

        def load(url: str) -> bool:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except PlaywrightError:
                return False
            return self._wait_for_question(page)

        # Always ask for the exact page, *including page 1*.  Without page=1
        # the site restores the reader's remembered position: a subtopic run
        # then started 50 questions ahead, stored nothing, and marked itself
        # complete.
        if not load(topic_url(topic_id, page=target, tag_id=tag_id)):
            # Do not fetch page 1 after a failed target load: that doubles the
            # requests exactly when the site is already unhappy, and the error
            # page is still on screen for the caller to classify.
            return None
        cur, total = position()
        if cur == target:
            return cur
        if total is not None and target > total:
            # The counter says the list ends before the requested page:
            # this target really is finished.  -1 means "complete", which
            # the caller must not confuse with a navigation failure.
            return -1
        if cur is None:
            # The target page loaded but had no counter: fall back to page 1
            # once, then walk blindly if the counter is still missing.
            self._polite_delay()
            if not load(topic_url(topic_id, page=1, tag_id=tag_id)):
                return None
            cur, total = position()
            if cur is None:
                for _ in range(1, target):
                    if self._next_state(page) != "ready" or not self._click_next(page):
                        return None
                    self._polite_delay()
                return target
            if total is not None and target > total:
                return -1
        else:
            applog.log(f"[position] requested page {target}, site shows {cur} "
                       "— walking from there instead of reloading page 1")

        if total is not None and target > total:
            return -1
        # Never start ahead of the target, even by one question.
        while cur > target:
            if not self._click_nav(page, "prev"):
                applog.log(f"[position] cannot move back to page {target}; "
                           f"site still shows {cur} — pausing instead of "
                           f"skipping the earlier questions")
                return None
            self._polite_delay()
            nxt, _ = position()
            if nxt is None or nxt >= cur:
                return None
            cur = nxt
        while cur < target:
            if self._next_state(page) != "ready":
                return None
            if not self._click_next(page):
                return None
            self._polite_delay()
            nxt, _ = position()
            if nxt is None or nxt <= cur:
                return None
            cur = nxt
        return cur

    # -- question persistence ------------------------------------------------

    def _store_assets(self, conn, q: ParsedQuestion) -> None:
        """Download this question's images and point the stored HTML at them.

        Downloads used to be written to disk while the stored markup kept the
        site's absolute URLs, so the offline archive rendered broken images.
        Rewriting to ``assets/<file>`` works in both viewers: the local server
        serves it under /assets/, and the exported bundle ships the same files
        next to index.html.
        """
        url_map: dict[str, str] = {}
        for img in q.images:
            existing = get_asset_by_url(conn, img.original_url)
            if existing:
                img.sha1, img.local_path = existing["sha1"], existing["local_path"]
                if img.sha1 and img.local_path and Path(img.local_path).name:
                    url_map[img.original_url] = f"assets/{Path(img.local_path).name}"
                continue
            try:
                sha1, path, size, mime = download_asset(
                    img.original_url, assets_dir=self.assets_dir, role=img.role)
                img.sha1, img.local_path, img.bytes_size, img.mime = sha1, path, size, mime
                upsert_asset(conn, sha1, img.original_url, path, mime, size, img.role)
                url_map[img.original_url] = f"assets/{Path(path).name}"
            except Exception as ex:
                print(f"[!] asset download failed ({img.original_url[:80]}): {ex}")

        if url_map:
            q.stem_html = rewrite_asset_urls(q.stem_html, url_map)
            if q.explanation_html:
                q.explanation_html = rewrite_asset_urls(q.explanation_html, url_map)

    def _classify_topic_path(self, conn, page: Page, topic_id: int) -> None:
        """Classify this topic from the breadcrumb on its own question page.

        Runs once per topic before the first stored question, so
        ``category_path`` is written even when hierarchy discovery never ran
        or failed — the page itself prints the full path (category » year »
        section » topic) in its header.
        """
        try:
            segs = extract_topic_path(page.content())
            if segs:
                from medo.db import ensure_topic_path
                path = ensure_topic_path(conn, topic_id, segs)
                self._topic_paths = None  # rebuild with the new chain
                print(f"[+] topic {topic_id} classified: {path}")
            else:
                print(f"[i] topic {topic_id}: no on-page breadcrumb found; "
                      "keeping the fallback grouping.")
        except Exception as ex:
            print(f"[!] topic {topic_id}: breadcrumb classification failed: {ex}")

    def _record_question(self, conn, q: ParsedQuestion, topic_id: int,
                         tag_id: Optional[int] = None,
                         subtopic_title: Optional[str] = None) -> int:
        self._store_assets(conn, q)
        if self._topic_paths is None:
            from medo.db import build_topic_paths
            self._topic_paths = build_topic_paths(conn)
        cat_path = self._topic_paths.get(f"/qbank/topic-id-{topic_id}")
        if tag_id is not None:
            q.source_url = topic_url(topic_id, tag_id=tag_id)
        qid = upsert_question(
            conn, q, topic_id=topic_id, category_path=cat_path,
            tag_id=tag_id, subtopic_title=subtopic_title,
            download_bytes=self._bytes_this_question,
            fetched_as=self.fetched_as,
        )
        self._question_times.append(time.monotonic())
        self._question_bytes.append(self._bytes_this_question)
        self._bytes_this_question = 0
        self.questions_total += 1
        # Persist the learned range periodically, not on every question: a
        # crash should not lose the fact that a 429 happened.
        if self.pace.clean_pages and self.pace.clean_pages % 25 == 0:
            self._persist_pace()
        return qid

    def _maybe_print_estimate(self, scraped_count: int) -> None:
        if self._estimate_printed or scraped_count < 20:
            return
        self._estimate_printed = True
        span = self._question_times[-1] - self._question_times[0]
        n = max(1, len(self._question_times) - 1)
        sec_per_q = span / n
        kb_per_q = self.measured_avg_bytes / 1024
        total_sec = self.elapsed_minutes * 60
        rate = (scraped_count / total_sec) if total_sec > 5 else sec_per_q
        eta_1k = (1000 / rate) / 60 if rate > 0 else float("inf")
        mb_total = self.bytes_total / (1024 * 1024)
        stats = self.stats()
        eta = (f"{stats['eta_seconds'] // 60} min"
               if stats.get("eta_seconds") is not None else "unknown")
        print(
            f"\n[≈] Estimate after {scraped_count} questions: "
            f"{sec_per_q:.1f} s/question, {kb_per_q:.0f} KB/question, "
            f"session total {mb_total:.1f} MB in {self.elapsed_minutes:.1f} min\n"
            f"    → projected {eta_1k:.0f} min per 1000 questions "
            f"at the current pace (incl. polite delays).\n"
            f"    live: {stats['requests_per_minute']}/min, "
            f"delay {stats['delay_low']:.1f}-{stats['delay_high']:.1f}s, "
            f"429 today {stats['throttles_429_today']}, "
            f"{stats['questions_per_hour']} q/hour, ETA {eta}"
        )

    # -- main loop ------------------------------------------------------------

    # -- cursor (topic or subtopic) ------------------------------------------

    @staticmethod
    def _read_state(conn, topic_id: int, tag_id: Optional[int]) -> Optional[dict]:
        from medo.db import get_target_state
        return get_target_state(conn, topic_id, tag_id)

    @staticmethod
    def _write_state(conn, topic_id: int, tag_id: Optional[int], last_page: int,
                     status: str, *, total_pages: Optional[int] = None,
                     error_message: Optional[str] = None) -> None:
        """Store the cursor where the next resume will look for it.

        A subtopic is a *filtered view* of its topic's pool, so its page 7 is
        not the topic's page 7: sharing ``scrape_state`` made each resume start
        at the wrong place and each finished subtopic look completed.
        """
        from medo.db import update_micro_state, update_scrape_state
        if tag_id is None:
            update_scrape_state(conn, topic_id=topic_id, last_page=last_page,
                                total_pages=total_pages, status=status,
                                error_message=error_message)
        else:
            update_micro_state(conn, topic_id=topic_id, tag_id=int(tag_id),
                               last_page=last_page, total_pages=total_pages,
                               status=status, error_message=error_message)

    def scrape_topic(
        self,
        topic_id: int,
        start_page: Optional[int] = None,
        max_pages: Optional[int] = None,
        refill: bool = False,
        tag_id: Optional[int] = None,
        subtopic_title: Optional[str] = None,
        skip_known: bool = False,
    ) -> dict:
        """Scrape one topic or one of its subtopics («?tag=»).

        Resumes from the matching cursor unless ``start_page`` is given.

        ``skip_known`` is the reconciliation pass after a topic's subtopics:
        questions already stored with a site id are counted and passed over
        without revealing or re-storing them, so only the ones no subtopic
        page showed are paid for.
        """
        self._setup_signal_handlers()
        applog.install_tee()  # mirror all prints into logs/medo.log
        if self.max_minutes is not None and self._deadline is None:
            self._deadline = self.run_started + self.max_minutes * 60.0

        conn = get_connection(self.db_path)
        init_db(conn)
        state = self._read_state(conn, topic_id, tag_id)
        known_ids: set[int] = set()
        if skip_known:
            known_ids = {
                int(r[0]) for r in conn.execute(
                    "SELECT site_question_id FROM questions WHERE topic_id = ? "
                    "AND site_question_id IS NOT NULL", (topic_id,))
            }
            print(f"[i] topic {topic_id}: {len(known_ids)} question(s) already "
                  "stored — looking only for the missing ones")

        current_page = start_page if start_page is not None else (
            ((state["last_page"] if state else 0) or 0) + 1
        )
        if refill:
            current_page = start_page if start_page is not None else 1

        self._write_state(conn, topic_id, tag_id, current_page - 1, "running")
        label = f"Topic {topic_id}" + (f" tag {tag_id}" if tag_id else "")
        print(f"[*] {label}: starting at page {current_page}"
              f"{' (refill)' if refill else ''}")

        scraped = 0
        skipped_known = 0
        consecutive_errors = 0
        backoff = BACKOFF_BASE
        status = "completed"
        detail = ""

        with sync_playwright() as p:
            browser, page = self._new_page(p)
            try:
                try:
                    actual = self._position_at(page, topic_id, current_page, tag_id)
                    if actual == -1:
                        self._write_state(conn, topic_id, tag_id,
                                          max(current_page - 1, 0), "completed")
                        raise ScraperStopped("completed",
                                             "topic shorter than resume target")
                    if actual is None:
                        body = page.locator("body").inner_text(timeout=8000)
                        kind = classify_page(body, has_question=False)
                        guest_quota = is_guest_quota(body)
                        code = document_status(page)
                        if code == 429:
                            kind = "throttled"
                            # Widen the pace before the retry the user makes
                            # after the pause: the next run starts wider only
                            # if it is the same process (the log says so).
                            wait = self.pace.throttled()
                            self._throttle_times.append(time.time())
                            self._persist_pace()
                            self._start_cooldown(wait)
                        # What the site actually served, straight into the log:
                        # without it every failure read «could not position» and
                        # the real answer (429 / login / empty) was unknowable.
                        applog.log(f"[diag] {label} page {current_page} "
                                   f"http={code} url={page.url} "
                                   f"body={normalize_single_line(body)[:200]!r}")
                        # The guest allowance redirects to /login too, but the
                        # account is not the problem — the daily quota is.
                        if "/login" in page.url and not guest_quota:
                            kind = "auth"  # anti-bot redirect to the login page
                        elif guest_quota:
                            kind = "limit"
                        status = {"limit": "limit", "auth": "auth_required",
                                  "blocked": "blocked",
                                  "throttled": "throttled"}.get(kind, "paused")
                        detail = (
                            # An empty-looking page is NOT proof that the list
                            # ended: a slow render or a changed widget looks
                            # the same.  Keep the cursor resumable instead of
                            # recording a finished topic that has 1 of 39.
                            f"could not position at page {current_page}"
                            if kind == "empty" else
                            ("browser rejected by the site (403)"
                             if kind == "blocked" else
                             (f"site rate-limited this connection (HTTP {code})"
                              if kind == "throttled" else
                              ("guest quota exhausted"
                               if guest_quota else kind))))
                        raise ScraperStopped(status, detail)
                    if actual != current_page:
                        # The session was already ahead of (or behind) the
                        # request: follow the page the site actually shows so
                        # stored page numbers and the cursor stay truthful.
                        print(f"[i] {label}: requested page {current_page}, "
                              f"site is at {actual}")
                        current_page = actual
                        self._write_state(conn, topic_id, tag_id,
                                          max(current_page - 1, 0), "running")
                except ScraperStopped:
                    raise
                except PlaywrightError as ex:
                    raise ScraperStopped("error", f"navigation failed: {ex}")

                while True:
                    reason = self._stop_reason(scraped)
                    if reason:
                        raise ScraperStopped(reason)

                    q_started = time.monotonic()
                    # Do NOT reset the byte counter here: the response for the
                    # page the previous iteration navigated to arrives after
                    # that question was recorded, and it belongs to *this*
                    # question.  _record_question resets it after counting.

                    try:
                        q = self._parse_with_retry(page, topic_id, current_page)
                    except PlaywrightError as ex:
                        consecutive_errors += 1
                        if consecutive_errors > MAX_CONSECUTIVE_ERRORS:
                            raise ScraperStopped("error", str(ex))
                        time.sleep(backoff)
                        backoff = min(backoff * 2, BACKOFF_MAX)
                        continue

                    if q is None:
                        # No question rendered: classify why, then stop.
                        body = page.locator("body").inner_text(timeout=8000)
                        kind = classify_page(body, has_question=False)
                        guest_quota = is_guest_quota(body)
                        if "/login" in page.url and not guest_quota:
                            kind = "auth"  # anti-bot redirect to the login page
                        elif guest_quota:
                            kind = "limit"
                        if kind == "throttled" or document_status(page) == 429:
                            wait = self.pace.throttled()
                            self._throttle_times.append(time.time())
                            self._persist_pace()
                            self._start_cooldown(wait)
                            raise ScraperStopped(
                                "throttled",
                                f"site rate-limited this connection — "
                                f"cool-down {self.cooldown_seconds}s recorded")
                        if kind == "blocked":
                            raise ScraperStopped(
                                "blocked",
                                "the site rejected the browser (403) — "
                                "open a download again to retry")
                        if kind == "limit":
                            raise ScraperStopped(
                                "limit",
                                "guest quota exhausted" if is_guest_quota(body)
                                else "quota/upgrade message detected")
                        if kind == "auth":
                            raise ScraperStopped("auth_required", "session missing or expired")
                        # Only the page's own counter may declare the list
                        # finished.  Anything else stays resumable, otherwise
                        # a transient render failure marked a 1-of-39 subtopic
                        # as «کامل» and threw away the continuation.
                        cur, total = read_question_position(body)
                        if cur is not None and total is not None and cur >= total:
                            raise ScraperStopped("completed",
                                                 f"end of list at page {current_page}")
                        raise ScraperStopped("paused",
                                             f"no question at page {current_page}")

                    if known_ids and q.site_question_id in known_ids:
                        # Already stored through a subtopic: no reveal, no
                        # write.  Keep the position truthful and move on.
                        skipped_known += 1
                        if self._next_state(page) != "ready" or not self._click_next(page):
                            raise ScraperStopped("completed",
                                                 f"end of list at page {current_page}")
                        current_page += 1
                        self._polite_delay()
                        continue

                    # Answer fallback (only when the marker did not give it)
                    if needs_submit(q):
                        submitted = self._submit_for_answer(page, topic_id, current_page)
                        if submitted is not None and submitted.stem_text:
                            q = submitted
                    elif q.explanation_status is not ExplanationStatus.FULL:
                        # Explanation not stored yet: reveal it if possible.
                        q = self._reveal_explanation(page, topic_id, current_page, q)

                    if q.explanation_status == ExplanationStatus.LOCKED:
                        # This page is quota-locked: store what we have, keep
                        # going; a site-wide limit message stops us below.
                        body = page.locator("body").inner_text(timeout=8000)
                        if any(ph in body for ph in LIMIT_PHRASES):
                            self._write_state(
                                conn, topic_id, tag_id, current_page - 1, "limit",
                                error_message="quota message on page")
                            raise ScraperStopped("limit", "quota message detected")

                    if topic_id not in self._topics_classified:
                        self._topics_classified.add(topic_id)
                        self._classify_topic_path(conn, page, topic_id)

                    qid = self._record_question(
                        conn, q, topic_id, tag_id=tag_id,
                        subtopic_title=subtopic_title)
                    scraped += 1
                    self.pace.succeeded()
                    self.delay_min, self.delay_max = self.pace.range
                    consecutive_errors, backoff = 0, BACKOFF_BASE
                    self._write_state(conn, topic_id, tag_id, current_page, "running")
                    if self.on_progress:
                        self.on_progress(current_page, qid, q.stem_text[:40])
                    print(f"[+] topic {topic_id}{f' tag {tag_id}' if tag_id else ''} "
                          f"p{current_page}: "
                          f"{q.stem_text[:50]}... [{q.explanation_status.value}]")

                    if max_pages and scraped >= max_pages:
                        raise ScraperStopped("max_pages")
                    reason = self._stop_reason(scraped)
                    if reason:
                        raise ScraperStopped(reason)
                    self._maybe_print_estimate(scraped)

                    # --- navigation ---
                    nxt_state = self._next_state(page)
                    if nxt_state != "ready":
                        detail = f"next button {nxt_state} at page {current_page}"
                        raise ScraperStopped("completed", detail)
                    if not self._click_next(page):
                        detail = f"next did not advance at page {current_page}"
                        raise ScraperStopped("completed", detail)
                    current_page += 1
                    self._polite_delay()

            except ScraperStopped as stop:
                status, detail = stop.status, stop.message
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

        self._finalize_state(conn, topic_id, status, detail, tag_id=tag_id)

        conn.close()
        if status in {"limit", "auth_required"}:
            self._print_lacking_explanations(db_path=self.db_path)
        tail = get_connection(self.db_path)
        try:
            final_state = self._read_state(tail, topic_id, tag_id) or {}
        finally:
            tail.close()
        return {
            "scraped_count": scraped,
            "skipped_known": skipped_known,
            "last_page": int(final_state.get("last_page") or 0),
            "status": status,
            "detail": detail,
            "bytes": self.bytes_total,
            "elapsed_min": round(self.elapsed_minutes, 2),
        }

    @staticmethod
    def _finalize_state(conn, topic_id: int, status: str,
                        detail: str = "", tag_id: Optional[int] = None) -> None:
        """Commit the run's outcome to its cursor table.

        The stored cursor — not ``current_page - 1`` — is the truth: it is
        written every time a question is saved.  On a resumed run that stops
        at its very first page, ``current_page - 1`` pointed one page *before*
        the last stored question, so a completed single-page (or just-resumed)
        run was recorded as complete with nothing downloaded.
        """
        row = Harvester._read_state(conn, topic_id, tag_id) or {}
        last_stored = int(row.get("last_page") or 0)
        if status == "completed":
            Harvester._write_state(conn, topic_id, tag_id, last_stored,
                                   "completed", total_pages=last_stored)
        elif status in {"interrupted", "max_minutes", "max_questions", "max_pages"}:
            Harvester._write_state(conn, topic_id, tag_id, last_stored, "paused")
        else:  # limit / auth_required / blocked / error
            Harvester._write_state(conn, topic_id, tag_id, last_stored, status,
                                   error_message=detail)

    def scrape_many(self, topic_ids: list[int], max_pages_per_topic: Optional[int] = None) -> dict:
        """Sequentially scrape several topics; stops on any clean-stop status."""
        totals = {"scraped_count": 0, "topics_done": 0, "stopped": None}
        for tid in topic_ids:
            result = self.scrape_topic(tid, max_pages=max_pages_per_topic)
            totals["scraped_count"] += result["scraped_count"]
            totals["topics_done"] += 1
            if result["status"] in {"interrupted", "max_minutes", "max_questions",
                                    "limit", "auth_required", "blocked", "error"}:
                totals["stopped"] = {"topic": tid, **result}
                break
        return totals

    def refill_explanations(self, topic_ids: Optional[list[int]] = None) -> dict:
        """Revisit questions whose explanation_status != 'full'.

        Safe to run later (e.g. after a Pro subscription): it only re-reads
        pages the account is shown; it never evades any limit.
        """
        conn = get_connection(self.db_path)
        init_db(conn)
        targets = topic_ids or topics_needing_refill(conn)
        conn.close()
        if not targets:
            print("[+] Nothing to refill — all stored explanations are complete.")
            return {"scraped_count": 0, "topics": []}
        print(f"[*] Refill: {len(targets)} topic(s) need fuller explanations.")
        results = []
        for tid in targets:
            if self._interrupted or (self._deadline and time.monotonic() >= self._deadline):
                break
            r = self.scrape_topic(tid, start_page=1, refill=True)
            results.append(r)
            if r["status"] in {"limit", "auth_required", "error"}:
                break
        return {"scraped_count": sum(r["scraped_count"] for r in results),
                "topics": [r["status"] for r in results]}

    # -- helpers -------------------------------------------------------------

    @staticmethod
    def _print_lacking_explanations(db_path: Path | str) -> None:
        import sqlite3
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT topic_id, count(*) AS n FROM questions
                WHERE explanation_status != 'full'
                GROUP BY topic_id ORDER BY n DESC
            """).fetchall()
            conn.close()
        except Exception:
            return
        if rows:
            print("[i] Topics still lacking full explanations (run "
                  "`medo scrape --refill-explanations` later):")
            for r in rows:
                print(f"    topic {r['topic_id']}: {r['n']} question(s)")


def get_state_last_page(db_path: Path | str, topic_id: int) -> int:
    conn = get_connection(db_path)
    state = get_scrape_state(conn, topic_id)
    conn.close()
    return (state or {}).get("last_page", 0)
