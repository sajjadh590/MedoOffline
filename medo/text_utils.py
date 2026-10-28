"""
medo/text_utils.py
Text normalization utilities.

Strips invisible Unicode chars, normalises Arabic glyphs → Persian,
applies NFKC normalization, collapses whitespace.

One shared definition lives here: the site injects variation selectors
(U+FE00..U+FE0F) and bidi controls between letters to poison scrapers, and
earlier copies of this set in three modules missed them.
"""
import re
import unicodedata

# Invisible / zero-width code points that carry no visual meaning.
# ZWNJ (U+200C) is deliberately NOT here: it is real Persian orthography
# («می‌شود»), so text shown to the reader keeps it; only hashing, option
# signatures and search normalisation strip it.
INVISIBLE_RANGES = (
    "\u00ad"          # SOFT HYPHEN
    "\u034f"          # COMBINING GRAPHEME JOINER
    "\u061c"          # ARABIC LETTER MARK
    "\u200b"          # ZERO WIDTH SPACE
    "\u200d-\u200f"   # ZWJ, LEFT-TO-RIGHT MARK, RIGHT-TO-LEFT MARK
    "\u202a-\u202e"   # bidi embedding / override
    "\u2060-\u2069"   # WORD JOINER … INVISIBLE SEPARATOR
    "\ufe00-\ufe0f"   # VARIATION SELECTORS (the site's obfuscation)
    "\ufeff"          # BOM / ZERO WIDTH NO-BREAK SPACE
)
INVISIBLE_RE = re.compile(f"[{INVISIBLE_RANGES}]")
INVISIBLE_OR_ZWNJ_RE = re.compile(f"[{INVISIBLE_RANGES}\u200c]")

# Arabic forms → Persian canonical
_ARABIC_MAP = str.maketrans(
    {
        "\u0643": "\u06a9",  # ك → ک
        "\u064a": "\u06cc",  # ي → ی
        "\u0649": "\u06cc",  # ى → ی (alef maqsura)
    }
)


def strip_invisible(text: str, *, keep_zwnj: bool = False) -> str:
    """Remove invisible / zero-width Unicode characters.

    ZWNJ is removed unless ``keep_zwnj`` is true — display fields pass true so
    Persian half-spaces survive, while hashes and signatures keep the old,
    canonical (ZWNJ-free) form.
    """
    pattern = INVISIBLE_RE if keep_zwnj else INVISIBLE_OR_ZWNJ_RE
    return pattern.sub("", text)


def normalize_text(text: str) -> str:
    """
    Full normalization pipeline:
      1. Strip invisible characters
      2. NFKC normalization
      3. Arabic ي/ك → Persian ی/ک
      4. Collapse runs of whitespace (preserve single newlines if desired)
    """
    text = strip_invisible(text)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ARABIC_MAP)
    # Collapse runs of non-newline whitespace; strip leading/trailing
    text = re.sub(r"[^\S\n]+", " ", text).strip()
    return text


def normalize_single_line(text: str) -> str:
    """Normalize and also collapse newlines → single space (for options etc.)."""
    return re.sub(r"\s+", " ", normalize_text(text)).strip()


def content_hash_input(stem: str, options: list[str]) -> str:
    """Return a canonical string for hashing stem+options for deduplication."""
    opts_str = "|".join(normalize_single_line(o) for o in options)
    return f"{normalize_single_line(stem)}||{opts_str}"
