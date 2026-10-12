"""
tests/test_pdf.py
Unit tests for medo/pdf.py: Jinja2 template rendering and PDF generation.
"""
from medo.pdf import render_book_html


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
