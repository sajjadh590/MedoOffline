"""
medo/html_utils.py
HTML sanitization and manipulation utilities.
"""
import html as _html
import re
from html.parser import HTMLParser
from typing import Optional

# Allowed tags for sanitized HTML
ALLOWED_TAGS = frozenset({
    "div", "p", "br", "b", "strong", "i", "em", "u",
    "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "span", "img", "sub", "sup",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    "mark",
})

# Allowed attributes per tag (None = allow all from the set)
ALLOWED_ATTRS: dict[str, set[str]] = {
    "div": {"class", "style"},
    "img": {"src", "alt", "width", "height", "class"},
    "span": {"style", "class"},
    "i": {"style"},
    "b": {"style"},
    "strong": {"style"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

# Style properties allowed in inline styles
ALLOWED_STYLE_PROPS = {"color", "font-weight", "font-style", "text-decoration"}

# Obfuscation: zero-width / invisible characters injected between letters
_INVIS_RE = re.compile(r"[\u200b-\u200f\u2060-\u2064\ufeff]")


def _attr(name: str, value: str) -> str:
    """One serialized attribute, fully escaped.

    ``HTMLParser`` already decodes character references in attribute values,
    so a value like ``class='a" onmouseover="alert(1)'`` arrives as
    ``a" onmouseover="alert(1)``.  Interpolating that verbatim closed the
    attribute early and injected a live event handler into the stored HTML —
    which the UI then renders with ``innerHTML``.  Re-encoding is what keeps
    the sanitizer's allow-list actually binding.
    """
    return f'{name}="{_html.escape(str(value), quote=True)}"'


def _filter_style(style_value: str) -> str:
    """Keep only allowed CSS properties from inline style string."""
    parts = []
    for decl in style_value.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        if ":" not in decl:
            continue
        prop, _, val = decl.partition(":")
        prop = prop.strip().lower()
        if prop in ALLOWED_STYLE_PROPS:
            parts.append(f"{prop}:{val.strip()}")
    return "; ".join(parts)


class _Sanitizer(HTMLParser):
    """Streaming HTML sanitizer."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._output: list[str] = []
        # Tag name actually emitted for each open element;
        # None means the element (and its subtree) is suppressed.
        self._open_stack: list[Optional[str]] = []

    @staticmethod
    def _highlight_form(style_value: str) -> Optional[str]:
        """'mark' when the inline style colours text, 'b' when it only bolds."""
        for decl in style_value.split(";"):
            if ":" not in decl:
                continue
            prop, _, val = decl.partition(":")
            prop = prop.strip().lower()
            val = val.strip().lower()
            if prop == "color" and val and val not in {"inherit", "initial", "unset", "none"}:
                return "mark"
            if prop == "font-weight" and val in {"bold", "700", "800", "900"}:
                return "b"
        return None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        tag_l = tag.lower()
        void_tags = {"br", "img", "input", "hr", "meta", "link"}
        attr_dict = dict(attrs)

        classes = [c.lower() for c in (attr_dict.get("class") or "").split()]
        elem_id = (attr_dict.get("id") or "").lower()
        is_noise = (
            "watermark" in classes
            or any(c in {"yektanet", "mediaad"} for c in classes)
            or elem_id in {"pointpopup", "yektanet-ad", "mediaad"}
        )

        suppressed = (
            bool(self._open_stack and self._open_stack[-1] is None)
            or is_noise
            or tag_l not in ALLOWED_TAGS
        )
        if suppressed:
            if tag_l not in void_tags:
                self._open_stack.append(None)
            return

        # Default deny: only explicitly allow-listed attributes survive
        # (previously unlisted tags allowed ALL attributes, which leaked
        # `inert` and junk `data-*="base64 CSS"` into stored HTML).
        allowed = ALLOWED_ATTRS.get(tag_l, set())
        filtered_attrs: list[str] = []
        raw_style = attr_dict.get("style") or ""

        for key, val in attr_dict.items():
            if val is None:
                continue
            if allowed is not None and key not in allowed:
                continue
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]*", key or ""):
                continue  # never let a crafted name through
            if key == "style":
                val = _filter_style(val)
                if not val:
                    continue
            filtered_attrs.append(_attr(key, val))

        # Inline highlight <i style="color:...;font-weight:700"> -> <mark>/<b>
        emit = tag_l
        if tag_l in {"i", "span", "em", "u"} and "style" in attr_dict:
            form = self._highlight_form(raw_style)
            if form:
                emit = form
                filtered_attrs = [a for a in filtered_attrs
                                  if not a.startswith("style=")]

        if tag_l not in void_tags:
            self._open_stack.append(emit)

        attr_str = (" " + " ".join(filtered_attrs)) if filtered_attrs else ""
        self._output.append(f"<{emit}{attr_str}>")

    def handle_endtag(self, tag: str):
        tag_l = tag.lower()
        void_tags = {"br", "img", "input", "hr", "meta", "link"}
        if tag_l in void_tags:
            return
        if not self._open_stack:
            return
        emitted = self._open_stack.pop()
        if emitted:
            self._output.append(f"</{emitted}>")

    def handle_data(self, data: str):
        if not (self._open_stack and self._open_stack[-1] is None):
            self._output.append(_INVIS_RE.sub("", data))

    def handle_entityref(self, name: str):
        if not (self._open_stack and self._open_stack[-1] is None):
            self._output.append(f"&{name};")

    def handle_charref(self, name: str):
        if not (self._open_stack and self._open_stack[-1] is None):
            self._output.append(f"&#{name};")

    def get_output(self) -> str:
        return "".join(self._output)


def sanitize_html(html: str) -> str:
    """Return sanitized HTML with only allowed tags/attrs."""
    p = _Sanitizer()
    p.feed(html)
    return p.get_output().strip()


def html_to_text(html: str) -> str:
    """Strip all HTML tags and return plain text."""
    # Preserve line breaks from block elements
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"</(p|div|li|h\d)>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", "", html)
    # Decode common entities
    html = html.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    html = html.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    # Collapse whitespace
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def rewrite_asset_urls(html: str, url_map: dict[str, str]) -> str:
    """Replace original image src URLs with local asset paths."""
    for orig, local in url_map.items():
        html = html.replace(f'src="{orig}"', f'src="{local}"')
        html = html.replace(f"src='{orig}'", f"src='{local}'")
    return html
