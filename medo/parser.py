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
import re
import unicodedata
from html.parser import HTMLParser
from typing import Optional

from medo.html_utils import html_to_text, sanitize_html
from medo.models import AnswerSource, ExplanationStatus, ParsedImage, ParsedOption, ParsedQuestion
from medo.text_utils import normalize_single_line, normalize_text, strip_invisible

# ---------------------------------------------------------------------------
# Low-level HTML helpers (pure-Python, no external deps required for tests)
# ---------------------------------------------------------------------------

_INVIS_RE = re.compile(r"[\u200b-\u200f\u2060-\u2064\ufeff]")


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
_WRONG_COLOR   = "rgb(239, 64, 86)"
_COLOR_RE      = re.compile(r"color:\s*(rgb\(\d[\d,\s]*\))", re.IGNORECASE)
_IMG_SRC_RE    = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)


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
        if eid in {"pointPopup", "yektanet-ad", "mediaad"}:
            return True
        cls = attrs.get("class", "").lower().split()
        if any(c in {"watermark", "yektanet", "mediaad"} for c in cls):
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
        return html

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


def _collect_css_rules(html: str) -> list[tuple[Optional[str], list[str], dict, int]]:
    """All parsable rules from every <style> block, in document order."""
    rules: list[tuple[Optional[str], list[str], dict, int]] = []
    order = 0
    for block in _STYLE_BLOCK_RE.findall(html):
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


def _is_visible(node: dict, rules: list) -> bool:
    """display!=none, visibility:visible, not parked off-screen, for the whole
    ancestor chain (a hidden parent hides every child)."""
    cur: Optional[dict] = node
    while cur is not None and cur.get("tag") != "#root":
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
    return len(text) >= 5


def _first_visible_paragraph(dom: "_MiniDom", root: dict, rules: list) -> Optional[dict]:
    """First visible <p> with real text; returns its wrapping block div when
    there is one (so inner_html keeps the <p>), else the <p> itself."""
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
        if parent is not None and parent.get("tag") in {"div", "section", "article"}:
            return parent
        return el
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
    return _first_visible_paragraph(dom, dom._root, rules)


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
    """Strip invisible chars, NFKC-normalise, collapse whitespace."""
    raw = _INVIS_RE.sub("", raw)
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
    expl_html_raw  = dom.inner_html(block_b) if block_b else ""
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

    # Strategy A: parse (تایید گزینه N) marker from explanation text
    if expl_text:
        m = _MARKER_RE.search(expl_text)
        if m:
            marker_answer = int(m.group(1))
    # Also search raw HTML in case invisible chars disrupted text
    if marker_answer is None and expl_html_raw:
        raw_stripped = _INVIS_RE.sub("", expl_html_raw)
        m = _MARKER_RE.search(raw_stripped)
        if m:
            marker_answer = int(m.group(1))

    # Strategy B (submitted only): read label inline colour
    if submitted:
        for opt in options:
            label_node = dom.find_by_id(f"label{opt.index}")
            if label_node:
                style = label_node.get("attrs", {}).get("style", "")
                color = _parse_inline_color(style)
                if color:
                    # Normalise: strip spaces inside rgb()
                    color_norm = re.sub(r"\s+", " ", color).strip()
                    if _colors_match(color_norm, _CORRECT_COLOR):
                        color_answer = opt.index
                        break

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
        expl_lower = expl_text.lower()
        if any(kw in expl_text for kw in ("اشتراک", "پرو", "Pro", "ورود کنید", "تهیه اشتراک")):
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
