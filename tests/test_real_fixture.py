"""tests/test_real_fixture.py
Validation against the REAL page saved from medofast.ir (Chrome MHTML).

The file is git-ignored (scraped content) but present locally at
tests/fixtures/real_before.mhtml — every test skips when it is absent.

Key findings these tests lock in (see NOTES.md "Real DOM"):
  - There is NO #q-stem-container; the stem lives in #qtxt as a random-class
    div, siblings are decoys (including a full copy of another question),
    all hidden/revealed by <style> blocks with randomised class selectors.
  - Options, vote percents, marker and explanation work as specified.
"""
from pathlib import Path

import pytest

from medo.models import AnswerSource, ExplanationStatus
from medo.parser import parse_mhtml_file

FIXTURE = Path(__file__).parent / "fixtures" / "real_before.mhtml"
URL = "https://medofast.ir/qbank/topic-id-79"

pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="real_before.mhtml not present (scraped content)"
)


@pytest.fixture(scope="module")
def q():
    return parse_mhtml_file(
        str(FIXTURE), submitted=False, source_url=URL, source_page=1, ordinal=1
    )


class TestRealFixture:
    def test_stem_extracted(self, q):
        assert q.stem_text, "stem must not be empty on the real page"
        assert "دیابت" in q.stem_text          # diabetes (the topic)
        assert "متفورمین" in q.stem_text        # metformin
        assert "Hb" in q.stem_text or "A1c" in q.stem_text

    def test_stem_is_not_decoy(self, q):
        # Decoy copy inside #qtxt carries a different (hematuria) question
        assert "هماچوری" not in q.stem_text
        assert "نفرکتومی" not in q.stem_text

    def test_options(self, q):
        assert [o.index for o in q.options] == [1, 2, 3, 4]
        assert "Thiazolidinedione" in q.options[0].text
        assert "SGLT2" in q.options[2].text
        assert [o.vote_percent for o in q.options] == [2, 3, 86, 8]

    def test_answer_from_marker(self, q):
        assert q.correct_index == 3
        assert q.answer_source == AnswerSource.MARKER

    def test_explanation(self, q):
        assert q.explanation_text and len(q.explanation_text) > 50
        assert q.explanation_status == ExplanationStatus.FULL
        assert "SGLT2" in q.explanation_text

    def test_no_invisible_chars_in_text_fields(self, q):
        from medo.text_utils import strip_invisible
        # ZWNJ is real Persian orthography, so it is allowed (and required)
        # in display text; every other invisible code point is not.
        for field in (q.stem_text, q.explanation_text or "",
                      *(o.text for o in q.options)):
            assert field == strip_invisible(field, keep_zwnj=True)

    def test_no_obfuscation_junk_in_html_fields(self, q):
        for html in (q.stem_html, q.explanation_html or ""):
            assert "inert" not in html
            assert "data-" not in html          # base64-CSS junk attributes
            assert "display: none" not in html  # decoy CSS must not leak

    def test_hash_deterministic(self):
        q1 = parse_mhtml_file(str(FIXTURE), submitted=False, source_url=URL,
                              source_page=1, ordinal=1)
        q2 = parse_mhtml_file(str(FIXTURE), submitted=False, source_url=URL,
                              source_page=1, ordinal=1)
        assert q1.content_hash == q2.content_hash and len(q1.content_hash) == 64
