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


# Persian and Arabic-Indic digits, in the order of their ASCII equivalents.
_DIGIT_MAP = str.maketrans(
    "\u06f0\u06f1\u06f2\u06f3\u06f4\u06f5\u06f6\u06f7\u06f8\u06f9"
    "\u0660\u0661\u0662\u0663\u0664\u0665\u0666\u0667\u0668\u0669",
    "0123456789" * 2,
)


def normalize_search_text(text: str) -> str:
    """One canonical form for searching — shared by Python, the web UI and Android.

    Arabic glyphs become Persian, ZWNJ and invisible code points disappear,
    Persian/Arabic digits become ASCII, whitespace collapses and case is
    folded.  The same rules live in ``medo/web/normalize.js`` and
    ``android/src/ir/medo/app/Text.java``; ``tests/search_vectors.json`` is the
    contract all three are tested against.
    """
    return normalize_single_line(text or "").translate(_DIGIT_MAP).lower()


def content_hash_input(stem: str, options: list[str]) -> str:
    """Return a canonical string for hashing stem+options for deduplication."""
    opts_str = "|".join(normalize_single_line(o) for o in options)
    return f"{normalize_single_line(stem)}||{opts_str}"
