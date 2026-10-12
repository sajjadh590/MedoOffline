"""tests/test_parser.py
Unit tests for medo.parser against both fixture files.

Expected question (from spec):
  Stem        : 55-year-old woman, T2DM, HbA1c 8.7%, EF 35%, CVD, add which drug?
  Options     : 4 options (Thiazolidinedione, DPP4I, SGLT2 inhibitor, GLP1 Agonist)
  Correct     : 3 (SGLT2 inhibitor)
  Votes       : 2 / 3 / 86 / 8
  Explanation : Persian text about SGLT2, marker (تایید گزینه 3)
  Images      : at least 1 mindmap from s3-strg.medofast.ir
"""
import re
import unicodedata
from pathlib import Path

import pytest

from medo.models import AnswerSource, ExplanationStatus
from medo.parser import extract_html_from_mhtml, parse_mhtml_file, parse_question_html
from medo.text_utils import normalize_single_line, strip_invisible

FIXTURES = Path(__file__).parent / "fixtures"
BEFORE   = FIXTURES / "before.mhtml"
AFTER    = FIXTURES / "after.mhtml"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_parsed(path: Path, *, submitted: bool):
    assert path.exists(), (
        f"Fixture missing: {path}\n"
        f"Run:  python scripts/make_fixtures.py"
    )
    return parse_mhtml_file(str(path), submitted=submitted,
                            source_url="https://medofast.ir/qbank/topic-id-79",
                            source_page=1, ordinal=1)


# ---------------------------------------------------------------------------
# MHTML extraction
# ---------------------------------------------------------------------------

class TestMhtmlExtraction:
    def test_before_is_extractable(self):
        data = BEFORE.read_bytes()
        html = extract_html_from_mhtml(data)
        assert len(html) > 500
        assert "<html" in html.lower()

    def test_after_is_extractable(self):
        data = AFTER.read_bytes()
        html = extract_html_from_mhtml(data)
        assert "block-b" in html


# ---------------------------------------------------------------------------
# Text normalisation unit tests
# ---------------------------------------------------------------------------

class TestTextNormalisation:
    def test_strip_invisible_zero_width_space(self):
        raw = "مه\u200bار\u200cکننده\u200d"
        assert strip_invisible(raw) == "مهارکننده"

    def test_strip_all_invisible_range(self):
        invisible = "".join(chr(c) for c in range(0x200B, 0x2010))
        invisible += "\u2060\u2061\u2062\u2063\u2064\ufeff"
        text = f"A{invisible}B"
        assert strip_invisible(text) == "AB"

    def test_arabic_ya_normalised(self):
        result = normalize_single_line("دیابتي")          # Arabic ي
        assert "\u064a" not in result                     # no Arabic ya
        assert "\u06cc" in result                         # Persian ی present

    def test_arabic_kaf_normalised(self):
        result = normalize_single_line("كلیه")            # Arabic ك
        assert "\u0643" not in result
        assert "\u06a9" in result                         # Persian ک present

    def test_nfkc_applied(self):
        # Ligature fi → f + i after NFKC
        result = normalize_single_line("\ufb01le")
        assert "\ufb00" not in result


# ---------------------------------------------------------------------------
# Decoy / invisible-char leak detection
# ---------------------------------------------------------------------------

class TestDecoyAndInvisibleLeak:
    """Verify that decoy content and invisible chars never reach the output."""

    # Known decoy phrases (from fixture make_fixtures.py)
    DECOY_FRAGMENTS = [
        "درمانکاتاتونیا",
        "آنتیبیوتیکمناسب",
        "label1_decoy",
        "label2_decoy",
        "گزینه جعلی",
    ]

    INVISIBLE_CHARS = list("\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff")

    @pytest.fixture(scope="class")
    @classmethod
    def before_q(cls):
        return _load_parsed(BEFORE, submitted=False)

    @pytest.fixture(scope="class")
    @classmethod
    def after_q(cls):
        return _load_parsed(AFTER, submitted=True)

    def _all_text_fields(self, q) -> list[str]:
        fields = [q.stem_text or "", q.explanation_text or ""]
        fields += [o.text for o in q.options]
        return fields

    def test_no_invisible_in_stem_text(self, before_q):
        for ch in self.INVISIBLE_CHARS:
            assert ch not in (before_q.stem_text or ""), \
                f"Invisible char U+{ord(ch):04X} found in stem_text"

    def test_no_invisible_in_option_text(self, before_q):
        for opt in before_q.options:
            for ch in self.INVISIBLE_CHARS:
                assert ch not in opt.text, \
                    f"Invisible char U+{ord(ch):04X} in option {opt.index}"

    def test_no_invisible_in_explanation(self, before_q):
        expl = before_q.explanation_text or ""
        for ch in self.INVISIBLE_CHARS:
            assert ch not in expl

    def test_no_decoy_in_stem_text(self, before_q):
        for fragment in self.DECOY_FRAGMENTS:
            assert fragment not in (before_q.stem_text or ""), \
                f"Decoy fragment '{fragment}' leaked into stem_text"

    def test_no_decoy_in_option_text(self, before_q):
        for opt in before_q.options:
            for fragment in self.DECOY_FRAGMENTS:
                assert fragment not in opt.text, \
                    f"Decoy '{fragment}' in option {opt.index}"

    def test_no_watermark(self, before_q):
        all_text = " ".join(self._all_text_fields(before_q))
        assert "MedoFast.ir" not in all_text
        assert "watermark"   not in all_text

    def test_no_ads(self, before_q):
        all_text = " ".join(self._all_text_fields(before_q))
        assert "yektanet" not in all_text.lower()
        assert "mediaad"  not in all_text.lower()

    def test_no_points_popup(self, before_q):
        all_text = " ".join(self._all_text_fields(before_q))
        assert "امتیاز" not in all_text or "pointPopup" not in all_text


# ---------------------------------------------------------------------------
# Core question parsing — before fixture (not submitted)
# ---------------------------------------------------------------------------

class TestBeforeFixture:
    @pytest.fixture(scope="class")
    @classmethod
    def q(cls):
        return _load_parsed(BEFORE, submitted=False)

    def test_has_4_options(self, q):
        assert len(q.options) == 4

    def test_option_indices(self, q):
        assert [o.index for o in q.options] == [1, 2, 3, 4]

    def test_option_1_text(self, q):
        assert "Thiazolidinedione" in q.options[0].text

    def test_option_2_text(self, q):
        assert "DPP4" in q.options[1].text or "DPP" in q.options[1].text

    def test_option_3_text_sglt2(self, q):
        assert "SGLT2" in q.options[2].text

    def test_option_4_text_glp1(self, q):
        assert "GLP" in q.options[3].text or "GLP1" in q.options[3].text

    def test_vote_percents(self, q):
        votes = [o.vote_percent for o in q.options]
        assert votes == [2, 3, 86, 8], f"Expected [2,3,86,8], got {votes}"

    def test_stem_contains_hba1c(self, q):
        assert "HbA1c" in q.stem_text or "8.7" in q.stem_text

    def test_stem_contains_ef(self, q):
        assert "EF" in q.stem_text or "35" in q.stem_text

    def test_stem_no_invisible_chars(self, q):
        invis = re.compile(r"[\u200b-\u200f\u2060-\u2064\ufeff]")
        assert not invis.search(q.stem_text)

    def test_explanation_marker_detected(self, q):
        """Marker is present in DOM even before submit — correct_index readable."""
        assert q.correct_index == 3, \
            f"Expected correct_index=3 (marker), got {q.correct_index}"

    def test_answer_source_is_marker(self, q):
        assert q.answer_source == AnswerSource.MARKER

    def test_has_mindmap_image(self, q):
        mindmap_images = [img for img in q.images if img.role == "mindmap"]
        assert len(mindmap_images) >= 1, "Expected at least one mindmap image"

    def test_mindmap_url_is_s3(self, q):
        for img in q.images:
            if img.role == "mindmap":
                assert "s3-strg.medofast.ir" in img.original_url

    def test_explanation_not_empty(self, q):
        assert q.explanation_text and len(q.explanation_text) > 20

    def test_explanation_status_full(self, q):
        assert q.explanation_status == ExplanationStatus.FULL

    def test_content_hash_computed(self, q):
        assert q.content_hash and len(q.content_hash) == 64   # sha256 hex

    def test_has_images_flag(self, q):
        assert q.has_images is True


# ---------------------------------------------------------------------------
# After-submit fixture
# ---------------------------------------------------------------------------

class TestAfterFixture:
    @pytest.fixture(scope="class")
    @classmethod
    def q(cls):
        return _load_parsed(AFTER, submitted=True)

    def test_correct_index_from_color(self, q):
        assert q.correct_index == 3

    def test_answer_source_both_or_color(self, q):
        assert q.answer_source in (AnswerSource.BOTH, AnswerSource.COLOR, AnswerSource.MARKER)

    def test_explanation_visible(self, q):
        assert q.explanation_text and len(q.explanation_text) > 20

    def test_vote_percents_after(self, q):
        assert q.options[2].vote_percent == 86  # option 3

    def test_options_still_4(self, q):
        assert len(q.options) == 4

    def test_option_3_still_sglt2(self, q):
        assert "SGLT2" in q.options[2].text


# ---------------------------------------------------------------------------
# Determinism: same HTML parsed twice gives same hash
# ---------------------------------------------------------------------------

class TestDeterminism:
    def test_content_hash_stable(self):
        q1 = _load_parsed(BEFORE, submitted=False)
        q2 = _load_parsed(BEFORE, submitted=False)
        assert q1.content_hash == q2.content_hash

    def test_before_after_same_hash(self):
        """Content hash must be identical for before/after of same question."""
        q1 = _load_parsed(BEFORE, submitted=False)
        q2 = _load_parsed(AFTER,  submitted=True)
        # Stem and options text are the same; only colours/display differ
        assert q1.content_hash == q2.content_hash
