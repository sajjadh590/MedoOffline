"""
tests/test_pdf.py
Unit tests for medo/pdf.py: Jinja2 template rendering and PDF generation.
"""
from medo.pdf import _embed_local_images, render_book_html


def _question(n, path=None, explanation=True):
    q = {
        "stem_html": f"<p>سوال {n}</p>",
        "stem_text": f"سوال {n}",
        "options": [
            {"index": 1, "text": "الف"},
            {"index": 2, "text": "ب"},
            {"index": 3, "text": "ج"},
            {"index": 4, "text": "د"},
        ],
        "correct_index": 1,
        "explanation_html": (f"<p>پاسخ {n}</p>" if explanation else None),
        "explanation_text": (f"پاسخ {n}" if explanation else None),
    }
    if path is not None:
        q["book_path"] = path
    return q


def test_book_has_a_numbered_toc_in_tree_order():
    """Sections print in hierarchy order and the فهرست lists them by number."""
    questions = [
        _question(1, "ریه"),
        _question(2, "اطفال » نفرولوژی"),
    ]
    html = render_book_html(
        questions,
        section_order={"ریه": 0, "اطفال » نفرولوژی": 1},
    )

    assert "فهرست مطالب" in html
    toc = html[html.index("فهرست مطالب"):html.index("بخش اول")]
    assert "ریه" in toc and "اطفال » نفرولوژی" in toc
    assert toc.count("1 سوال") == 2       # one count per section

    # Both sections print in tree order, numbered to match the TOC.
    body = html[html.index("بخش اول"):]
    assert body.index("1. ریه") < body.index("2. اطفال » نفرولوژی")


def test_section_order_overrides_insertion_order():
    questions = [_question(1, "ریه"), _question(2, "قلب")]
    html = render_book_html(
        questions,
        section_order={"ریه": 5, "قلب": 0},
    )
    assert html.index("1. قلب") < html.index("2. ریه")


def test_question_numbers_are_sequential_in_printed_order():
    """Renumbering after the section sort keeps question numbers and the answer
    key aligned with what the reader sees."""
    questions = [_question(1, "ریه"), _question(2, "قلب")]
    html = render_book_html(questions, section_order={"ریه": 1, "قلب": 0})

    heart = html[html.index("1. قلب"):html.index("2. ریه")]
    assert "سوال شماره 1" in heart
    assert "سوال شماره 2" in html[html.index("2. ریه"):]


def test_missing_explanations_are_not_repeated_per_question():
    """A book of mostly answer-only questions must not print one placeholder
    card per question; one summary sentence replaces them."""
    questions = [
        _question(1, "ریه", explanation=False),
        _question(2, "ریه", explanation=False),
        _question(3, "ریه", explanation=True),
    ]
    html = render_book_html(questions, include_answers=True)

    assert "توضیحات تشریحی ثبت نشده" not in html
    assert html.count('class="expl-card"') == 1
    assert "برای 2 سوال پاسخ تشریحی ثبت نشده است" in html
    # The answer-only questions are still in the answer key.
    assert html.count('<td>') >= 3


def test_explanations_can_be_omitted_entirely():
    questions = [_question(1, "ریه", explanation=False)]
    html = render_book_html(questions, include_answers=False)
    assert 'class="expl-card"' not in html
    assert "فهرست مطالب" in html


def test_render_book_html():
    questions = [
        {
            "stem_html": "<p>بیمار با تنگی نفس و سرفه مزمن مراجعه کرده است.</p>",
            "stem_text": "بیمار با تنگی نفس و سرفه مزمن مراجعه کرده است.",
            "options": [
                {"index": 1, "text": "آسم"},
                {"index": 2, "text": "COPD"},
                {"index": 3, "text": "برونشکتازی"},
                {"index": 4, "text": "آمبولی ریه"},
            ],
            "correct_index": 2,
            "explanation_html": "<p>پاسخ تشریحی کامل برای COPD با اسپیرومتری.</p>",
            "explanation_text": "پاسخ تشریحی کامل برای COPD با اسپیرومتری.",
        }
    ]

    html = render_book_html(questions, title="آزمون ریه", include_answers=True)
    assert "آزمون ریه" in html
    assert "بیمار با تنگی نفس" in html
    assert "COPD" in html
    assert "پاسخ تشریحی" in html
    assert "dir=\"rtl\"" in html


def test_embedded_images_resolve_assets_folder(tmp_path):
    """Stored markup points at ``assets/<sha1>.webp``; the file actually lives
    under the data directory, so the fallback base must be used."""
    assets = tmp_path / "data" / "assets"
    assets.mkdir(parents=True)
    image = assets / "abc.webp"
    image.write_bytes(b"\x89PNG fake bytes")

    html = '<p><img src="assets/abc.webp" alt=""></p>'
    out = _embed_local_images(html, tmp_path, tmp_path / "data")
    assert out.startswith('<p><img src="data:image/webp;base64,')

    # A missing file is left untouched instead of producing a broken data URI.
    assert _embed_local_images('<img src="assets/missing.webp">',
                               tmp_path, tmp_path / "data") == '<img src="assets/missing.webp">'


def test_answer_key_prints_inside_its_own_section():
    """Each chapter's key follows that chapter, not the whole book at the end."""
    questions = [_question(1, "ریه"), _question(2, "ریه"), _question(3, "قلب")]
    html = render_book_html(
        questions, section_order={"ریه": 0, "قلب": 1})

    lung = html.index("1. ریه")
    lung_key = html.index("پاسخ‌نامهٔ بخش 1")
    heart = html.index("2. قلب")
    heart_key = html.index("پاسخ‌نامهٔ بخش 2")

    assert lung < lung_key < heart < heart_key
    assert "بخش دوم: پاسخ‌نامه کلیدی" not in html
    assert "بخش سوم: پاسخ‌نامه تشریحی" not in html


def test_section_key_uses_printed_question_numbers():
    """Numbers in a chapter's key continue the book's numbering."""
    questions = [_question(1, "ریه"), _question(2, "ریه"), _question(3, "قلب")]
    html = render_book_html(
        questions, section_order={"ریه": 0, "قلب": 1})

    lung_key = html[html.index("پاسخ‌نامهٔ بخش 1"):html.index("2. قلب")]
    assert "<td>1</td>" in lung_key and "<td>2</td>" in lung_key
    heart_key = html[html.index("پاسخ‌نامهٔ بخش 2"):]
    assert "<td>3</td>" in heart_key
