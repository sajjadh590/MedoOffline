"""
medo/parser.py
Static HTML parser for medofast.ir question pages.

Reads the HTML (extracted from an MHTML file or from a live Playwright page)
and returns a ParsedQuestion.

Key rules (from site analysis):
  - Never select by class name (they are randomised per page load).
  - Never rely on the `inert` attribute (real content AND decoys can both be inert).
  - Option text ground truth: span.encoded-option[data-encoded-text] (base64).
  - Vote percents: #prograssBar{i}[data-encoded-percent] (base64).
  - Correct answer: regex "تایید گزینه\\s*(\\d)" in #block-b text (marker);
    confirmed by label inline colour after submit (blue = correct, red = wrong).
  - Strip invisible Unicode chars before any text comparison.
  - Ignore: decoy blocks (hidden by randomised CSS), ads, watermark, popups.
"""
from __future__ import annotations

import base64
import logging
import re
import unicodedata
from html.parser import HTMLParser
from typing import Optional

from medo.html_utils import html_to_text, sanitize_html
from medo.models import AnswerSource, ExplanationStatus, ParsedImage, ParsedOption, ParsedQuestion
from medo.text_utils import INVISIBLE_RE, normalize_single_line, strip_invisible

# ---------------------------------------------------------------------------
# Low-level HTML helpers (pure-Python, no external deps required for tests)
# ---------------------------------------------------------------------------

# One shared set (text_utils) — the old local copies missed the variation
# selectors the site injects between letters.
_INVIS_RE = INVISIBLE_RE
log = logging.getLogger(__name__)


class _AttrExtractor(HTMLParser):
    """One-pass extractor: collects tag attributes, text, and inner-HTML by id."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        # id → {tag, attrs_dict, inner_text, inner_html}
        self.by_id: dict[str, dict] = {}
        # Stack of open tag contexts (list of (id|None, tag))
        self._stack: list[tuple[Optional[str], str, list]] = []
        # All visible text in document order (raw)
        self.all_text: list[str] = []

    # -- parsing --

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        eid = attr_dict.get("id")
        self._stack.append((eid, tag.lower(), []))
        if eid:
            self.by_id.setdefault(eid, {
                "tag": tag.lower(),
                "attrs": attr_dict,
                "inner_text": [],
                "inner_html": [],
            })

    def handle_endtag(self, tag: str) -> None:
        if not self._stack:
            return
        eid, open_tag, chunks = self._stack.pop()
        text_blob  = "".join(chunks)
        html_blob  = "".join(chunks)  # same — we accumulate raw html in chunks
        if eid and eid in self.by_id:
            self.by_id[eid]["inner_text"].append(text_blob)
            self.by_id[eid]["inner_html"].append(html_blob)
        # Propagate up
        if self._stack:
            self._stack[-1][2].append(text_blob)

    def handle_data(self, data: str) -> None:
        self.all_text.append(data)
        for ctx in self._stack:
            ctx[2].append(data)


def _decode_b64(s: str) -> str:
    """Decode a base64 string, return empty string on failure."""
    try:
        return base64.b64decode(s.encode()).decode("utf-8", errors="replace")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_MARKER_RE     = re.compile(r"تایید\s*گزینه\s*(\d)", re.UNICODE)
_CORRECT_COLOR = "rgb(48, 127, 226)"

# The answer is printed in several shapes across the site's templates and the
# owner's own explanations: «تایید گزینه ۳», «پاسخ صحیح: گزینهٔ ۲», «گزینه ۴
# صحیح است». Only forms that *state* a correct option are listed; «گزینه ۲
# نادرست است» must never be read as an answer.
_MARKER_RES = (
    re.compile(r"(?:تایید|تأیید)\s*گزینه\s*([0-9]{1,2})"),
    re.compile(r"پاسخ\s*(?:صحیح|درست)\s*[:：\-]?\s*گزینه\s*[ٔ\s]*([0-9]{1,2})"),
    re.compile(r"گزینه\s*[ٔ\s]*([0-9]{1,2})\s*(?:صحیح|درست)\s*(?:است|هست|می\u200cباشد)"),
)

# Class/data tokens that unambiguously mark the correct label. Deliberately
# narrow: a generic `answer`/`right` token appears on every option row.
_CORRECT_TOKENS = {
    "correct", "is-correct", "answer-correct", "option-correct",
    "right-answer", "true-answer", "fa-check", "fa-check-circle",
    "check-circle", "bi-check", "bi-check-circle", "icon-check",
}
_BENGALI_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_WRONG_COLOR   = "rgb(239, 64, 86)"
_COLOR_RE      = re.compile(r"color:\s*(rgb\(\d[\d,\s]*\))", re.IGNORECASE)
_IMG_SRC_RE    = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Site chrome (login / "your history on this test" widget)
#
# While the stem is still encrypted the only visible paragraphs on the page
# belong to this widget.  It must never be mistaken for a question stem —
# that is what produced stored "questions" such as
# «سابقه عملکرد شما در این تست: ...».
# ---------------------------------------------------------------------------

_WIDGET_TEXT_MARKERS: tuple[str, ...] = (
    "سابقه عملکرد شما در این تست",
    "لاگین سریع",
    "محاسبه هوشمند",
    "آخرین پاسخ دهی",
    # First navigation of a fresh session can serve a browser-check notice or
    # the site's SMS login page (also used as an anti-bot redirect) before the
    # single-page app boots; neither is ever a question.
    "پشتیبانی نمی",
    "مرورگر شما",
    "مرورگرهای پشتیبانی",
    "پشتیبانی شده",
    "کد تایید به شما پیامک",
    "سیاست حریم خصوصی",
    "در حال انتقال به سایت",
)

# Stable ids of chrome blocks (the widget also lives under `#strt`).
_NOISE_IDS: frozenset[str] = frozenset(
    {"pointPopup", "yektanet-ad", "mediaad", "strt", "statistics-title"}
)


def is_site_widget_text(text: str) -> bool:
    """True when `text` comes from site chrome rather than a question."""
    flat = normalize_single_line(text)
    return any(marker in flat for marker in _WIDGET_TEXT_MARKERS)


def is_valid_stem_text(text: str, min_len: int = 12) -> bool:
    """A storable stem: real question text, not site chrome.

    Navigation labels ("پنل کاربری", "بانک سوالات") are short and pass every
    structural check, so a floor on the length is part of the contract.
    """
    flat = normalize_single_line(text)
    return len(flat) >= min_len and not is_site_widget_text(flat)


# ---------------------------------------------------------------------------
# BeautifulSoup-free mini DOM reader using stdlib HTMLParser
# ---------------------------------------------------------------------------

class _MiniDom(HTMLParser):
    """
    Builds a simple tree of nodes so we can query by id without any
    external dependency.  Only needs to work on the specific structures
    we care about.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._root: dict = {"tag": "#root", "attrs": {}, "children": [], "text": ""}
        self._stack: list[dict] = [self._root]

    # -- element traversal helpers (called after full parse) --

    def _by_id(self, node: dict, eid: str) -> Optional[dict]:
        if node.get("attrs", {}).get("id") == eid:
            return node
        for child in node.get("children", []):
            result = self._by_id(child, eid)
            if result:
                return result
        return None

    def find_by_id(self, eid: str) -> Optional[dict]:
        return self._by_id(self._root, eid)

    @staticmethod
    def _is_noise(node: dict) -> bool:
        tag = node.get("tag", "")
        if tag == "#text":
            return False
        attrs = node.get("attrs", {})
        eid = attrs.get("id", "")
        if eid in _NOISE_IDS:
            return True
        cls = attrs.get("class", "").lower().split()
        # `answer-watermark` holds the trailing «MedoFast.ir» stamp that used
        # to end every stored explanation.
        if any("watermark" in c for c in cls) or any(
            c in {"yektanet", "mediaad"} for c in cls
        ):
            return True
        return False

    def inner_html(self, node: Optional[dict]) -> str:
        """Return a simplified HTML reconstruction of a node's children."""
        if node is None or self._is_noise(node):
            return ""
        parts = []
        for child in node.get("children", []):
            if self._is_noise(child):
                continue
            tag = child.get("tag", "")
            if tag == "#text":
                parts.append(child.get("text", ""))
            else:
                attrs = child.get("attrs", {})
                attr_str = "".join(
                    f' {k}="{v}"' for k, v in attrs.items() if v is not None
                )
                void_tags = {"br", "img", "input", "hr", "meta", "link"}
                if tag in void_tags:
                    parts.append(f"<{tag}{attr_str}>")
                else:
                    parts.append(f"<{tag}{attr_str}>{self.inner_html(child)}</{tag}>")
        return "".join(parts)

    def inner_text(self, node: Optional[dict]) -> str:
        if node is None or self._is_noise(node):
            return ""
        parts = []
        for child in node.get("children", []):
            if self._is_noise(child):
                continue
            if child.get("tag") == "#text":
                parts.append(child.get("text", ""))
            else:
                parts.append(self.inner_text(child))
        return "".join(parts)

    # -- HTMLParser overrides --

    _VOID = {"area","base","br","col","embed","hr","img","input",
             "link","meta","param","source","track","wbr"}

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr_dict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        node = {"tag": tag.lower(), "attrs": attr_dict, "children": [],
                "parent": self._stack[-1]}
        self._stack[-1]["children"].append(node)
        if tag.lower() not in self._VOID:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        # Pop back to the matching open tag
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i]["tag"] == tag.lower():
                self._stack = self._stack[:i]
                break

    def handle_data(self, data: str) -> None:
        # Store as a text child node (preserves document order inside parent)
        if self._stack:
            self._stack[-1]["children"].append({"tag": "#text", "text": data})


# ---------------------------------------------------------------------------
# MHTML reader
# ---------------------------------------------------------------------------

def _cid_styles(msg) -> list[str]:
    """text/css parts addressed as `cid:` — Chrome stores page CSS that way."""
    styles: list[str] = []

    def walk(part) -> None:
        if part.is_multipart():
            for sub in part.get_payload():
                if not isinstance(sub, str):
                    walk(sub)
            return
        if part.get_content_type() != "text/css":
            return
        if not (part.get("Content-Location") or "").strip().lower().startswith("cid:"):
            return
        payload = part.get_payload(decode=True)
        if payload:
            styles.append(payload.decode(part.get_content_charset() or "utf-8",
                                          errors="replace"))

    walk(msg)
    return styles


def _inline_cid_styles(html: str, styles: list[str]) -> str:
    """Put the saved CSS into <head> so the cascade can be evaluated offline."""
    if not styles:
        return html
    block = "".join(f"<style>{css}</style>" for css in styles)
    head_end = html.lower().find("</head>")
    if head_end == -1:
        return block + html
    return html[:head_end] + block + html[head_end:]


def extract_html_from_mhtml(mhtml_bytes: bytes) -> str:
    """
    Extract the first text/html part from an MHTML file.

    Strategy:
      1. Try Python's email module (handles well-formed MIME).
      2. If that yields nothing (e.g. line-ending quirks on Windows),
         fall back to a regex-based base64 extraction directly from bytes.
    """
    import base64
    import email
    import email.policy

    # ---- Strategy 1: stdlib email parser ----
    # Normalise to LF-only so the parser works regardless of how the file
    # was saved (Chrome MHTML uses CRLF; our fixture uses LF).
    normalised = mhtml_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    msg = email.message_from_bytes(normalised, policy=email.policy.compat32)

    def _find_html(part) -> Optional[str]:
        ct = part.get_content_type()
        if ct == "text/html":
            payload = part.get_payload(decode=True)
            if payload:
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
        if part.is_multipart():
            for sub in part.get_payload():
                if not isinstance(sub, str):
                    result = _find_html(sub)
                    if result:
                        return result
        return None

    html = _find_html(msg)
    if html:
        return _inline_cid_styles(html, _cid_styles(msg))

    # ---- Strategy 2: regex fallback (handles malformed MIME) ----
    # Find a base64-encoded block that follows a "Content-Type: text/html" header.
    pattern = re.compile(
        rb"Content-Type:\s*text/html[^\n]*\n"   # CT header
        rb"(?:[^\n]+\n)*?"                       # other headers
        rb"\n"                                   # blank line
        rb"((?:[A-Za-z0-9+/=]+\n)+)",           # base64 body
        re.IGNORECASE,
    )
    m = pattern.search(normalised)
    if m:
        b64_block = m.group(1).replace(b"\n", b"")
        try:
            decoded = base64.b64decode(b64_block)
            return decoded.decode("utf-8", errors="replace")
        except Exception:
            pass

    # ---- Strategy 3: just find the HTML doctype / tags ----
    # Last resort: return raw bytes between <html and </html>
    start = normalised.find(b"<!DOCTYPE")
    if start == -1:
        start = normalised.lower().find(b"<html")
    end = normalised.lower().rfind(b"</html>")
    if start != -1 and end != -1:
        return normalised[start: end + 7].decode("utf-8", errors="replace")

    raise ValueError("No text/html content found in MHTML file")


# ---------------------------------------------------------------------------
# Obfuscation-aware visibility
#
# The live site injects <style> blocks (also mirrored into junk data-* attrs)
# that give EVERY candidate block — real stem and decoys alike — a random
# class.  Decoy classes get `display: none !important; left: -9999px ...`,
# the real block gets `display: block !important`.  Class names change on
# every page load, so we must evaluate the CSS cascade instead of guessing.
# ---------------------------------------------------------------------------

_STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.S | re.I)
_CSS_RULE_RE    = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _parse_declarations(body: str) -> dict[str, tuple[str, bool]]:
    """`color: red; display: none !important` -> {prop: (value, important)}"""
    decls: dict[str, tuple[str, bool]] = {}
    for chunk in body.split(";"):
        if ":" not in chunk:
            continue
        prop, _, value = chunk.partition(":")
        prop = prop.strip().lower()
        important = "!important" in value.lower()
        value = value.replace("!important", "").replace("!IMPORTANT", "").strip().lower()
        if prop and value:
            decls[prop] = (value, important)
    return decls


def _parse_compound(sel: str) -> Optional[tuple[Optional[str], list[str]]]:
    """Support `tag`, `.cls`, `.a.b`, `tag.cls` — anything else is skipped."""
    sel = sel.strip()
    if not sel or any(c in sel for c in ":[]>+~()#@*"):
        return None
    parts = sel.split(".")
    head = parts[0]
    tag: Optional[str] = None
    classes: list[str] = []
    if head:
        if not re.fullmatch(r"[a-zA-Z][\w-]*", head):
            return None
        tag = head.lower()
    for p in parts[1:]:
        if not re.fullmatch(r"[\w-]+", p, re.UNICODE):
            return None
        classes.append(p)
    if tag is None and not classes:
        return None
    return tag, classes


_CSS_NESTED_AT_RE = re.compile(r"@(media\s+print|keyframes)[^{]*\{(?:[^{}]*\{.*?\})*[^{}]*\}", re.S | re.I)


def _collect_css_rules(html: str) -> list[tuple[Optional[str], list[str], dict, int]]:
    """All parsable rules from every <style> block, in document order.

    ``@media print`` and ``@keyframes`` blocks are removed first: the site's
    main stylesheet hides <body> for printing, which would make every block
    look invisible.
    """
    rules: list[tuple[Optional[str], list[str], dict, int]] = []
    order = 0
    for block in _STYLE_BLOCK_RE.findall(html):
        block = _CSS_NESTED_AT_RE.sub("", block)
        for sel, body in _CSS_RULE_RE.findall(block):
            decls = _parse_declarations(body)
            if not decls:
                continue
            for part in sel.split(","):
                comp = _parse_compound(part)
                if comp:
                    rules.append((comp[0], comp[1], decls, order))
                    order += 1
    return rules


def _effective_decls(node: dict, rules: list) -> dict[str, str]:
    """Resolved declarations for one element (important > specificity > order)."""
    best: dict[str, tuple[tuple[int, int, int], str]] = {}
    tag = node.get("tag", "")
    classes = set(node.get("attrs", {}).get("class", "").split())
    for rtag, rclasses, decls, order in rules:
        if rtag and rtag != tag:
            continue
        if rclasses and not all(c in classes for c in rclasses):
            continue
        spec = len(rclasses) + (1 if rtag else 0)
        for prop, (value, important) in decls.items():
            key = (1 if important else 0, spec, order)
            cur = best.get(prop)
            if cur is None or key >= cur[0]:
                best[prop] = (key, value)
    inline = node.get("attrs", {}).get("style", "")
    if inline:
        for prop, (value, important) in _parse_declarations(inline).items():
            key = (1 if important else 0, 10_000, 10**9)
            cur = best.get(prop)
            if cur is None or key >= cur[0]:
                best[prop] = (key, value)
    return {p: v for p, (_k, v) in best.items()}


def _is_visible(node: dict, rules: list, stop: Optional[dict] = None) -> bool:
    """display!=none, visibility:visible, not parked off-screen, for the whole
    ancestor chain (a hidden parent hides every child).

    ``stop`` excludes that ancestor and everything above it: #block-b is
    display:none until the reader submits, which says nothing about the
    blocks inside it.
    """
    cur: Optional[dict] = node
    while cur is not None and cur.get("tag") != "#root" and cur is not stop:
        decls = _effective_decls(cur, rules)
        if decls.get("display") == "none":
            return False
        if decls.get("visibility") in {"hidden", "collapse"}:
            return False
        if decls.get("position") in {"absolute", "fixed"}:
            left = decls.get("left", "")
            try:
                if left.endswith("px") and abs(float(left[:-2])) >= 9999:
                    return False
            except ValueError:
                pass
        cur = cur.get("parent")
    return True


def _inside_noise(dom: "_MiniDom", node: dict) -> bool:
    cur: Optional[dict] = node
    while cur is not None and cur.get("tag") != "#root":
        if dom._is_noise(cur):
            return True
        cur = cur.get("parent")
    return False


def _walk(node: dict):
    """Yield element descendants in document order (excluding self)."""
    for child in node.get("children", []):
        if child.get("tag") != "#text":
            yield child
            yield from _walk(child)


def _p_text_ok(dom: "_MiniDom", p: dict) -> bool:
    text = normalize_single_line(dom.inner_text(p))
    return len(text) >= 5 and not is_site_widget_text(text)


def _first_visible_paragraph(dom: "_MiniDom", root: dict, rules: list) -> Optional[dict]:
    """First visible <p> with real question text; returns its wrapping block div
    when there is one (so inner_html keeps every sibling <p> of the stem, not
    just the first line), else the <p> itself.

    Blocks belonging to site chrome (login / statistics widget) are skipped.
    """
    for el in _walk(root):
        if el.get("tag") != "p":
            continue
        if _inside_noise(dom, el):
            continue
        if not _is_visible(el, rules):
            continue
        if not _p_text_ok(dom, el):
            continue
        parent = el.get("parent")
        candidate = el
        if parent is not None and parent.get("tag") in {"div", "section", "article"}:
            candidate = parent
        # The wrapper can still be chrome (e.g. its heading carries the
        # marker) — never hand such a block back as the stem.
        if is_site_widget_text(dom.inner_text(candidate)):
            continue
        return candidate
    return None


def find_stem_node(dom: "_MiniDom", rules: list) -> Optional[dict]:
    """Locate the real question-stem block, decoy-proof.

    Order: stable ids (synthetic fixtures / legacy markup) first, then the live
    site's containers (#qtxt → #questions-container → #questionContentWrapper),
    then a whole-document fallback.
    """
    for eid in ("q-stem-container", "q-stem"):
        node = dom.find_by_id(eid)
        if node is not None and _first_visible_paragraph(dom, node, rules) is not None:
            return node
    for eid in ("qtxt", "questions-container", "questionContentWrapper"):
        container = dom.find_by_id(eid)
        if container is None:
            continue
        p = _first_visible_paragraph(dom, container, rules)
        if p is not None:
            return p
        # The real stem lives inside these containers and is revealed by the
        # site's JS.  While it is still encrypted the container holds nothing
        # but chrome — never fall through to a document-wide search here, that
        # only finds navigation labels ("پنل کاربری") . Returning None lets the
        # caller wait for the reveal instead of storing junk.
        return None
    return _first_visible_paragraph(dom, dom._root, rules)


# ---------------------------------------------------------------------------
# Explanation blocks: the site hides decoys inside #block-b
#
# The Pro page ships 12 .element-container blocks; 3 come from unrelated
# topics and are hidden by a rule in a per-load stylesheet (display:none,
# opacity:0, left:-9999px).  Their data-element-id is a hyphenated UUID while
# the 9 real blocks use a plain 32-hex id.  Keeping them appended three
# foreign paragraphs to every lesson.
# ---------------------------------------------------------------------------

_UUID_STYLE_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_ELEMENT_CONTAINER = "element-container"


def _block_info(node: dict, rules: list, stop: Optional[dict] = None) -> dict:
    """Both hidden-signals for one .element-container."""
    attrs = node.get("attrs", {})
    element_id = attrs.get("data-element-id") or attrs.get("id", "")
    css_hidden = not _is_visible(node, rules, stop)
    uuid_style = bool(_UUID_STYLE_RE.match(element_id))
    marked_hidden = attrs.get("data-medo-hidden") == "1"
    if (css_hidden or marked_hidden) and not uuid_style:
        # A plain 32-hex block hidden by the page: it may be real content, so
        # say so once per block instead of silently losing it.
        log.warning(
            "explanation block %r hidden by the page but not UUID-styled",
            element_id,
        )
    return {
        "element_id": element_id,
        "order": attrs.get("data-order", ""),
        "css_hidden": css_hidden,
        "uuid_style": uuid_style,
        "marked_hidden": marked_hidden,
        "hidden": marked_hidden or css_hidden or uuid_style,
        "text": normalize_single_line(_node_text(node)),
    }


def _node_text(node: dict) -> str:
    if node.get("tag") == "#text":
        return node.get("text", "")
    return "".join(_node_text(child) for child in node.get("children", []))


def _containers(node: dict):
    """Every .element-container below `node`, at any depth."""
    for child in node.get("children", []):
        if child.get("tag") == "#text":
            continue
        if _ELEMENT_CONTAINER in child.get("attrs", {}).get("class", "").split():
            yield child
        yield from _containers(child)


def explanation_blocks(html: str) -> list[dict]:
    """Every .element-container in #block-b with its hidden-signals.

    Read-only: used by scripts/dev/check_captures.py and the parser tests to
    show which blocks a page offers and which ones the parser drops.
    """
    dom = _MiniDom()
    dom.feed(html)
    block_b = dom.find_by_id("block-b")
    if block_b is None:
        return []
    rules = _collect_css_rules(html)
    return [_block_info(node, rules, block_b) for node in _containers(block_b)]


def _explanation_html(dom: "_MiniDom", block_b: dict, rules: list) -> str:
    """#block-b with its decoy blocks removed.

    A block goes when the page marks it hidden (inline CSS rule or the live
    browser's data-medo-hidden), or when its id is a hyphenated UUID — the
    second signal is only trusted on pages that also carry plain 32-hex ids,
    so a template change cannot empty the whole lesson.
    """
    infos = {id(node): _block_info(node, rules, block_b)
             for node in _containers(block_b)}
    id_shape_usable = any(
        info["element_id"] and not info["uuid_style"] for info in infos.values()
    )

    def prune(node: dict) -> Optional[dict]:
        if node.get("tag") == "#text":
            return node
        if (not _is_visible(node, rules, block_b)
                or node.get("attrs", {}).get("data-medo-hidden") == "1"):
            return None
        info = infos.get(id(node))
        if info is not None and (
            info["marked_hidden"]
            or info["css_hidden"]
            or (id_shape_usable and info["uuid_style"])
        ):
            return None
        pruned = {
            "tag": node.get("tag", "div"),
            "attrs": node.get("attrs", {}),
            "children": [c for c in (prune(k) for k in node.get("children", []))
                         if c is not None],
        }
        if info is not None:
            # Decoys hold their hidden rule on an inner div, so the container
            # itself looks visible; once the hidden part is gone, an empty
            # text-only block is a decoy too.
            has_image = any(el.get("tag") == "img" for el in _walk(pruned))
            if not has_image and not normalize_single_line(dom.inner_text(pruned)):
                return None
        return pruned

    return dom.inner_html(prune(block_b))


# A lock notice is short and says so; a lesson that merely mentions «پروتئین»,
# «پروگنوز» or «Procalcitonin» is not locked.
_LOCK_TEXT_RES = (
    re.compile(r"اشتراک\s+(?:مدوفست\s+)?پرو"),
    re.compile(r"فعال\s*سازی\s+مدوفست\s+پرو"),
    re.compile(r"مشاهده\s+پاسخ\s+تشریحی"),
    re.compile(r"(?<![\w\u0600-\u06FF])پرو(?![\w\u0600-\u06FF])"),
    re.compile(r"(?<![\w])Pro(?![\w])", re.IGNORECASE),
    re.compile(r"ورود\s+کنید"),
)
_LOCK_TEXT_MAX = 200


def _is_locked_explanation(text: Optional[str], dom: "_MiniDom") -> bool:
    """True only when the page itself is the paywall / lock notice."""
    if dom.find_by_id("premium-access-denied") is not None:
        return True
    flat = normalize_single_line(text or "")
    if not flat or len(flat) >= _LOCK_TEXT_MAX:
        return False
    return any(pattern.search(flat) for pattern in _LOCK_TEXT_RES)


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def _extract_images(html: str, role: str) -> list[ParsedImage]:
    images = []
    for m in _IMG_SRC_RE.finditer(html):
        src = m.group(1).strip()
        if src and not src.startswith("data:"):
            images.append(ParsedImage(original_url=src, role=role))
    return images


def _clean_text(raw: str) -> str:
    """Strip invisible chars, NFKC-normalise, collapse whitespace.

    ZWNJ survives here: it is Persian orthography, not obfuscation.  Hash
    inputs and signatures still normalise it away (text_utils).
    """
    raw = strip_invisible(raw, keep_zwnj=True)
    raw = unicodedata.normalize("NFKC", raw)
    # Arabic ي/ك → Persian
    raw = raw.replace("\u0643", "\u06a9").replace("\u064a", "\u06cc").replace("\u0649", "\u06cc")
    raw = re.sub(r"[^\S\n]+", " ", raw).strip()
    return raw


def _parse_inline_color(style: str) -> Optional[str]:
    """Extract the first rgb(…) colour value from an inline style string."""
    m = _COLOR_RE.search(style)
    return m.group(1) if m else None


def parse_question_html(
    html: str,
    *,
    source_url: str = "",
    source_page: int = 1,
    ordinal: int = 1,
    submitted: bool = False,
) -> ParsedQuestion:
    """
    Parse a medofast question page HTML string.

    Parameters
    ----------
    html       : Full page HTML (already decoded from MHTML or fetched by Playwright).
    submitted  : True when parsing the "after" state (submit button was clicked).

    Returns
    -------
    ParsedQuestion with all fields populated from what is available in the HTML.
    """
    dom = _MiniDom()
    dom.feed(html)

    images: list[ParsedImage] = []

    # ------------------------------------------------------------------
    # 1. Stem — id anchors first, then CSS-visibility search (live pages have
    #    no stable stem id: real block and decoys are random-class siblings).
    # ------------------------------------------------------------------
    css_rules = _collect_css_rules(html)
    stem_node = find_stem_node(dom, css_rules)

    stem_html_raw = dom.inner_html(stem_node) if stem_node else ""
    stem_html     = sanitize_html(stem_html_raw)
    stem_text     = _clean_text(html_to_text(stem_html_raw))

    images += _extract_images(stem_html_raw, "question")

    # ------------------------------------------------------------------
    # 2. Options  (label#label{i}, span[data-encoded-text])
    # ------------------------------------------------------------------
    options: list[ParsedOption] = []
    for i in range(1, 6):  # support up to 5 options
        label_node = dom.find_by_id(f"label{i}")
        if label_node is None:
            break

        # Ground truth text from base64 encoded attribute
        # Find the span[data-encoded-text] child
        def _find_encoded_span(node: dict) -> Optional[str]:
            for child in node.get("children", []):
                attrs = child.get("attrs", {})
                enc = attrs.get("data-encoded-text")
                if enc:
                    return enc
                result = _find_encoded_span(child)
                if result:
                    return result
            return None

        enc_text = _find_encoded_span(label_node)
        if enc_text:
            option_text = _clean_text(_decode_b64(enc_text))
        else:
            option_text = _clean_text(dom.inner_text(label_node))

        # Vote percent from #prograssBar{i}[data-encoded-percent]
        bar_node   = dom.find_by_id(f"prograssBar{i}")
        vote_pct: Optional[int] = None
        if bar_node:
            enc_pct = bar_node.get("attrs", {}).get("data-encoded-percent", "")
            if enc_pct:
                try:
                    vote_pct = int(_decode_b64(enc_pct).strip())
                except (ValueError, TypeError):
                    pass

        label_html = dom.inner_html(label_node)
        options.append(ParsedOption(
            index       = i,
            text        = option_text,
            raw_html    = sanitize_html(label_html),
            vote_percent= vote_pct,
        ))

    # ------------------------------------------------------------------
    # 3. Explanation  (#block-b .qtext-after)
    # ------------------------------------------------------------------
    block_b = dom.find_by_id("block-b")
    expl_html_raw  = _explanation_html(dom, block_b, css_rules) if block_b else ""
    expl_html      = sanitize_html(expl_html_raw) if expl_html_raw else None
    expl_text      = _clean_text(html_to_text(expl_html_raw)) if expl_html_raw else None

    images += _extract_images(expl_html_raw, "explanation")
    # Mindmap specifically from #image-container img
    img_container = dom.find_by_id("image-container")
    if img_container:
        images += _extract_images(dom.inner_html(img_container), "mindmap")

    # Deduplicate images by URL (keep first occurrence, but upgrade role to mindmap if seen twice)
    seen_urls: dict[str, ParsedImage] = {}
    for img in images:
        if img.original_url not in seen_urls:
            seen_urls[img.original_url] = img
        elif img.role == "mindmap":
            seen_urls[img.original_url].role = "mindmap"
    images = list(seen_urls.values())

    # ------------------------------------------------------------------
    # 4. Correct answer detection
    # ------------------------------------------------------------------
    marker_answer: Optional[int] = None
    color_answer:  Optional[int] = None

    # Strategy A: parse the «correct option N» marker from the text, then from
    # raw HTML in case invisible chars or markup split it up.
    marker_answer = _answer_marker(expl_text)
    if marker_answer is None and expl_html_raw:
        marker_answer = _answer_marker(_INVIS_RE.sub("", expl_html_raw))

    # Strategy B (submitted only): the revealed label is coloured or carries a
    # check/correct class. Collect candidates and accept only a single one, so
    # a theme that paints every row cannot mark all four as the answer.
    if submitted:
        marked: list[int] = []
        for opt in options:
            label_node = dom.find_by_id(f"label{opt.index}")
            if not label_node:
                continue
            attrs = label_node.get("attrs", {})
            style = attrs.get("style", "") or ""
            color = _parse_inline_color(style)
            is_correct = False
            if color:
                color_norm = re.sub(r"\s+", " ", color).strip()
                is_correct = _colors_match(color_norm, _CORRECT_COLOR)
            if not is_correct and _label_is_correct(attrs, dom.inner_html(label_node)):
                is_correct = True
            if is_correct:
                marked.append(opt.index)
        if len(marked) == 1:
            color_answer = marked[0]

    # Determine authoritative answer
    correct_index: Optional[int] = None
    answer_source = AnswerSource.UNKNOWN

    if marker_answer and color_answer:
        correct_index = color_answer   # colour is authority per spec
        answer_source = (
            AnswerSource.BOTH if marker_answer == color_answer
            else AnswerSource.COLOR  # mismatch — log it, use colour
        )
    elif color_answer:
        correct_index = color_answer
        answer_source = AnswerSource.COLOR
    elif marker_answer:
        correct_index = marker_answer
        answer_source = AnswerSource.MARKER

    # ------------------------------------------------------------------
    # 5. Explanation status
    # ------------------------------------------------------------------
    if expl_text:
        if _is_locked_explanation(expl_text, dom):
            explanation_status = ExplanationStatus.LOCKED
        elif correct_index is not None and len(expl_text) < 30:
            explanation_status = ExplanationStatus.KEY_ONLY
        else:
            explanation_status = ExplanationStatus.FULL
    else:
        explanation_status = ExplanationStatus.UNKNOWN

    # ------------------------------------------------------------------
    # 6. Assemble result
    # ------------------------------------------------------------------
    q = ParsedQuestion(
        source_url         = source_url,
        source_page        = source_page,
        ordinal            = ordinal,
        stem_html          = stem_html,
        stem_text          = stem_text,
        options            = options,
        correct_index      = correct_index,
        answer_source      = answer_source,
        vote_percent       = [o.vote_percent for o in options],
        explanation_html   = expl_html,
        explanation_text   = expl_text,
        explanation_status = explanation_status,
        images             = images,
        has_images         = bool(images),
    )
    q.compute_content_hash()
    return q


# ---------------------------------------------------------------------------
# Colour comparison helper
# ---------------------------------------------------------------------------

def _rgb_to_tuple(rgb: str) -> Optional[tuple[int, int, int]]:
    """Parse 'rgb(R, G, B)' → (R, G, B) or None."""
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", rgb, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _answer_marker(text: Optional[str]) -> Optional[int]:
    """1-based option number stated as correct in text, or None."""
    if not text:
        return None
    normalised = text.translate(_BENGALI_DIGITS)
    for pattern in _MARKER_RES:
        for match in pattern.finditer(normalised):
            try:
                number = int(match.group(1))
            except (TypeError, ValueError):
                continue
            if 1 <= number <= 8:
                return number
    return None


def _label_is_correct(attrs: dict, inner_html: str) -> bool:
    """True when the label's class/data/icon marks it as the right answer."""
    for token in str(attrs.get("class", "")).lower().split():
        if token in _CORRECT_TOKENS:
            return True
    for key, value in attrs.items():
        name = str(key).lower()
        if name in {"data-correct", "data-answer", "data-is-correct", "aria-checked"}:
            if str(value).strip().lower() in {"1", "true", "yes", "correct"}:
                return True
    html = (inner_html or "").lower()
    return "fa-check" in html or "check-circle" in html


def _colors_match(a: str, b: str, tolerance: int = 5) -> bool:
    """Return True if two rgb() strings match within per-channel tolerance."""
    ta, tb = _rgb_to_tuple(a), _rgb_to_tuple(b)
    if ta is None or tb is None:
        return a.strip() == b.strip()
    return all(abs(x - y) <= tolerance for x, y in zip(ta, tb))


# ---------------------------------------------------------------------------
# Convenience: parse from MHTML file
# ---------------------------------------------------------------------------

def parse_mhtml_file(path: str, *, submitted: bool = False, **kwargs) -> ParsedQuestion:
    """Load an MHTML file from disk and parse it."""
    data = open(path, "rb").read()
    html = extract_html_from_mhtml(data)
    return parse_question_html(html, submitted=submitted, **kwargs)
