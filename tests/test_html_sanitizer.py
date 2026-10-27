"""tests/test_html_sanitizer.py

The sanitizer is the only thing between the scraped site and the local UI,
which renders ``stem_html``/``explanation_html`` with ``innerHTML``.  A
malformed attribute in the source page must never become a live attribute in
the stored copy.
"""
from medo.html_utils import sanitize_html


class TestAttributeEscaping:
    def test_quote_in_attribute_value_cannot_inject_a_handler(self):
        # HTMLParser decodes the single-quoted class value to
        # `a" onmouseover="alert(1)` — interpolating that raw closed the
        # attribute and added a real event handler.
        out = sanitize_html("<div class='a\" onmouseover=\"alert(1)'>سلام</div>")
        assert out == '<div class="a&quot; onmouseover=&quot;alert(1)">سلام</div>'
        assert '" onmouseover="' not in out

    def test_unknown_event_attributes_are_dropped(self):
        out = sanitize_html('<img src="x.png" onerror="alert(1)">')
        assert out == '<img src="x.png">'

    def test_encoded_quote_cannot_break_out_either(self):
        out = sanitize_html('<div class="a&#34; onload=&#34;x">t</div>')
        assert out == '<div class="a&quot; onload=&quot;x">t</div>'

    def test_crafted_attribute_name_is_ignored(self):
        out = sanitize_html('<span style="color:red" x-on:click="boom">متن</span>')
        assert "x-on:click" not in out


class TestAllowList:
    def test_script_and_iframe_are_removed_with_their_content(self):
        out = sanitize_html(
            "<div>قبل</div><script>alert(1)</script>"
            "<iframe src='//evil.example'></iframe><div>بعد</div>"
        )
        assert out == "<div>قبل</div><div>بعد</div>"

    def test_regular_markup_survives(self):
        html = "<p><strong>سوال</strong> <em>متن</em></p><ul><li>یک</li></ul>"
        assert sanitize_html(html) == html

    def test_style_keeps_only_allowed_properties(self):
        out = sanitize_html(
            '<div style="color: red; background: url(http://x/y.png)">م</div>'
        )
        assert out == '<div style="color:red">م</div>'

    def test_coloured_inline_highlight_becomes_mark(self):
        out = sanitize_html('<span style="color:#c00">واژه</span>')
        assert out == "<mark>واژه</mark>"
