"""
medo/text_utils.py
Text normalization utilities.

Strips invisible Unicode chars, normalises Arabic glyphs → Persian,
applies NFKC normalization, collapses whitespace.
"""
import re
import unicodedata

# Invisible / zero-width chars to strip
_INVIS_CHARS = (
    "\u200b"  # ZERO WIDTH SPACE
    "\u200c"  # ZERO WIDTH NON-JOINER
    "\u200d"  # ZERO WIDTH JOINER
    "\u200e"  # LEFT-TO-RIGHT MARK
    "\u200f"  # RIGHT-TO-LEFT MARK
    "\u2060"  # WORD JOINER
    "\u2061"  # FUNCTION APPLICATION
    "\u2062"  # INVISIBLE TIMES
    "\u2063"  # INVISIBLE SEPARATOR
    "\u2064"  # INVISIBLE PLUS
    "\ufeff"  # BOM / ZERO WIDTH NO-BREAK SPACE
)
_INVIS_RE = re.compile(f"[{''.join(_INVIS_CHARS)}]")

# Arabic forms → Persian canonical
_ARABIC_MAP = str.maketrans(
    {
        "\u0643": "\u06a9",  # ك → ک
        "\u064a": "\u06cc",  # ي → ی
        "\u0649": "\u06cc",  # ى → ی (alef maqsura)
    }
)


def strip_invisible(text: str) -> str:
    """Remove all invisible / zero-width Unicode characters."""
    return _INVIS_RE.sub("", text)


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
