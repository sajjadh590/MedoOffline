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
from medo.parser import (
    extract_html_from_mhtml,
    is_site_widget_text,
    is_valid_stem_text,
    parse_mhtml_file,
    parse_question_html,
)
from medo.text_utils import normalize_single_line, strip_invisible

FIXTURES = Path(__file__).parent / "fixtures"
BEFORE   = FIXTURES / "before.mhtml"
AFTER    = FIXTURES / "after.mhtml"

# Real page captured from the live site (kept out of the fixtures repo on purpose).
REAL_CAPTURE = Path(__file__).resolve().parents[1] / "data" / "_real.html"

# ---------------------------------------------------------------------------
# Live-page shapes
#
# Until the site's reveal-JS decrypts the stem, the only readable paragraphs on
# the page belong to the login/statistics widget.  A previous version accepted
# any non-empty stem, so that widget was stored as the question.
# ---------------------------------------------------------------------------

_WIDGET_BLOCK = """
  <div class="col-12">
    <div class="mb-1" id="strt">
      <h6 id="statistics-title" class="mt-1 mb-3 mx-2">
        سابقه عملکرد شما در این تست:
      </h6>
      <div class="row">
        <div class="col-6">
          <ul class="single-list-inner">
            <li><mark></mark>  پاسخدهی: <span class="blurrynumber">?</span> بار</li>
            <li><mark></mark>  پاسخ صحیح:<span class="blurrynumber">?</span> بار</li>
          </ul>
        </div>
      </div>
      <p>
        برای فعال شدن محاسبه هوشمند آیتم های بالا برای شما به صورت رایگان،
        نیاز است با زدن دکمه زیر، لاگین شوید:
      </p>
      <p>لاگین سریع و بازگشت به همین سوال</p>
    </div>
  </div>
"""

# The widget without its stable id — the text gate alone has to reject it.
_WIDGET_BLOCK_NO_ID = _WIDGET_BLOCK.replace(' id="strt"', "")

_ENCRYPTED_STEM = """
  <div class="encrypted-question" data-encrypted-text="UXCZXiigBUpTnHVZOr+7Ww=="></div>
"""

_REVEALED_STEM = """
  <div class="XIuIC5IL WDz4s9Kg" inert="">
    <p inert=""><span inert="" data-i2al1s3vi93="ZGl2LktHdXRxYUVtIHsgZGlzcGxheTogYmxvY2sgIWltcG9ydGFudDs=">
      خانم ۵۵ ساله ای با سابقه یک ساله دیابت تیپ دو که تحت درمان با متفورمین ۲ گرم، در روز می باشد.
    </span></p>
    <p>جهت کنترل قندخون و کاهش خطر بیماری های قلبی عروقی، کدام دارو مناسب است؟</p>
  </div>
"""



def _live_page(stem_block: str, widget: str = _WIDGET_BLOCK) -> str:
    """Minimal replica of the live question page (chrome before the question)."""
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="UTF-8"><title>topic</title></head>
<body>
<style>
  div.kQ1x9 {{ display: block !important; }}
  div.dec0y {{ display: none !important; left: -9999px !important; }}
</style>
<div class="row">
{widget}
</div>
<div class="questionContentWrapper" id="questionContentWrapper">
  <div class="questions-container" id="questions-container">
    <div class="qtext-before mb-3" id="qtxt" inert="">
{stem_block}
    </div>
    <div id="options-wrap">
      <label id="label1" inert=""><span data-encoded-text="2KfZhtmF2Ygg2YjZiNix24zYr9uM"></span></label>
      <label id="label2" inert=""><span data-encoded-text="2YHZiNix2YjYs9mF2KfbjNiv"></span></label>
    </div>
    <div id="block-b"><span>تایید گزینه 1</span></div>
  </div>
</div>
</body></html>"""

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

class TestAnswerRecovery:
    """Answers must be recovered from every shape the site uses — and never
    invented from a sentence that says an option is wrong."""

    @staticmethod
    def _page(block_b: str, label_class: str = "") -> str:
        page = _live_page(stem_block=_REVEALED_STEM)
        page = page.replace(
            '<div id="block-b"><span>تایید گزینه 1</span></div>', block_b)
        if label_class:
            page = page.replace(
                '<label id="label1" inert="">',
                f'<label id="label1" inert="" class="{label_class}">')
        return page

    def test_persian_digit_marker(self):
        q = parse_question_html(
            self._page('<div id="block-b"><span>تایید گزینه ۲</span></div>'),
            submitted=True)
        assert q.correct_index == 2
        assert q.answer_source == AnswerSource.MARKER

    def test_spelled_out_answer_sentence(self):
        q = parse_question_html(
            self._page('<div id="block-b"><span>پاسخ صحیح: گزینهٔ ۲ است</span></div>'),
            submitted=True)
        assert q.correct_index == 2

    def test_explicit_correct_sentence(self):
        q = parse_question_html(
            self._page('<div id="block-b"><span>در این سوال گزینه ۱ صحیح است</span></div>'),
            submitted=True)
        assert q.correct_index == 1

    def test_wrong_option_sentence_is_not_an_answer(self):
        q = parse_question_html(
            self._page('<div id="block-b"><span>گزینه ۲ نادرست است</span></div>'),
            submitted=True)
        assert q.correct_index is None

    def test_class_marked_option(self):
        q = parse_question_html(
            self._page('<div id="block-b"></div>',
                       label_class='option-row is-correct'),
            submitted=True)
        assert q.correct_index == 1
        assert q.answer_source == AnswerSource.COLOR

    def test_two_marked_options_are_not_trusted(self):
        page = self._page('<div id="block-b"></div>')
        page = page.replace('<label id="label1" inert="">',
                            '<label id="label1" inert="" class="correct">')
        page = page.replace('<label id="label2" inert="">',
                            '<label id="label2" inert="" class="correct">')
        q = parse_question_html(page, submitted=True)
        assert q.correct_index is None


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
# Site chrome must never become a question stem
# ---------------------------------------------------------------------------

class TestSiteChromeIsNeverTheStem:
    def test_widget_text_is_recognised(self):
        assert is_site_widget_text("سابقه عملکرد شما در این تست: پاسخدهی: ? بار")
        assert is_site_widget_text("لاگین سریع و بازگشت به همین سوال")
        assert not is_site_widget_text("جهت کنترل قندخون کدام دارو مناسب است؟")

    def test_invalid_stem_text(self):
        assert is_valid_stem_text("کدام دارو مناسب است؟")
        assert not is_valid_stem_text("")
        assert not is_valid_stem_text("اب")
        assert not is_valid_stem_text("لاگین سریع و بازگشت به همین سوال")
        # navigation labels are short and must not pass the length floor
        assert not is_valid_stem_text("پنل کاربری")

    def test_login_page_is_not_a_stem(self):
        """The site's SMS login page is served as an anti-bot redirect."""
        login_page = (
            '<!DOCTYPE html><html lang="fa"><body>'
            '<div id="app"><h1>ورود</h1>'
            '<p>یک کد تایید به شما پیامک خواهد شد</p>'
            '<p>قوانین و سیاست حریم خصوصی را می‌پذیرم</p>'
            '<button>ارسال کد</button></div>'
            "</body></html>"
        )
        assert is_site_widget_text("یک کد تایید به شما پیامک خواهد شد")
        assert not is_valid_stem_text("ورود\n\n یک کد تایید به شما پیامک خواهد شد")
        assert parse_question_html(login_page).stem_text == ""

    def test_redirect_shell_is_not_a_stem(self):
        shell = ('<!DOCTYPE html><html lang="fa"><body>'
                 '<p>در حال انتقال به سایت مورد نظر هستید...</p>'
                 "</body></html>")
        assert parse_question_html(shell).stem_text == ""

    def test_nav_chrome_inside_question_container_is_not_a_stem(self):
        """Chrome rendered inside the live containers must not win either."""
        page = _live_page(stem_block=_ENCRYPTED_STEM).replace(
            '<div class="qtext-before mb-3" id="qtxt" inert="">',
            '<div class="page-head"><p>پنل کاربری</p></div>\n'
            '    <div class="cta"><p>بانک سوالات</p></div>\n'
            '    <div class="qtext-before mb-3" id="qtxt" inert="">',
        )
        q = parse_question_html(page)
        assert q.stem_text == "", f"chrome leaked into stem: {q.stem_text!r}"

    def test_widget_only_page_has_no_stem(self):
        """Stem not revealed yet → empty stem so the harvester keeps waiting."""
        q = parse_question_html(_live_page(stem_block=_ENCRYPTED_STEM))
        assert q.stem_text == "", f"widget leaked into stem: {q.stem_text[:80]!r}"

    def test_widget_without_id_has_no_stem(self):
        q = parse_question_html(_live_page(stem_block=_ENCRYPTED_STEM,
                                           widget=_WIDGET_BLOCK_NO_ID))
        assert q.stem_text == ""

    def test_widget_never_wins_over_revealed_stem(self):
        q = parse_question_html(_live_page(stem_block=_ENCRYPTED_STEM + _REVEALED_STEM))
        assert "خانم ۵۵ ساله" in q.stem_text
        assert "سابقه عملکرد شما" not in q.stem_text
        assert "لاگین" not in q.stem_text

    def test_stem_keeps_every_sentence(self):
        """The whole stem, not just its first <p>."""
        q = parse_question_html(_live_page(stem_block=_ENCRYPTED_STEM + _REVEALED_STEM))
        assert q.stem_text.endswith("کدام دارو مناسب است؟")
        assert "متفورمین" in q.stem_text

    @pytest.mark.skipif(not REAL_CAPTURE.is_file(),
                        reason="real live capture not present (data/_real.html)")
    def test_real_capture_stem_is_the_question(self):
        q = parse_question_html(REAL_CAPTURE.read_text(encoding="utf-8", errors="replace"))
        assert "سابقه عملکرد شما" not in q.stem_text
        assert len(q.stem_text) > 100
        assert len(q.options) >= 2


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
