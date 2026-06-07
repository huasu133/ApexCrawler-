"""Tests for the citation reference system."""

import pytest
from apexcrawler.extraction.references import convert_to_citations, fast_urljoin


class TestConvertToCitations:
    def test_simple_link(self):
        md, refs = convert_to_citations("Read [docs](https://example.com)")
        assert "\u27e81\u27e9" in md
        assert "https://example.com" in refs

    def test_duplicate_url_same_number(self):
        md, refs = convert_to_citations("[a](https://x.com) and [b](https://x.com)")
        # Both should use the same citation number
        assert "\u27e81\u27e9" in md
        assert md.count("\u27e81\u27e9") == 2

    def test_image_link(self):
        md, refs = convert_to_citations("![img](https://x.com/pic.png)")
        assert "![" in md
        assert "\u27e81\u27e9" in md

    def test_empty_markdown(self):
        md, refs = convert_to_citations("")
        assert md == ""
        assert "## References" in refs

    def test_no_links(self):
        md, refs = convert_to_citations("Hello world")
        assert md == "Hello world"
        assert "## References" in refs  # Empty references section

    def test_multiple_unique_links(self):
        md, refs = convert_to_citations(
            "See [docs](https://example.com) and [blog](https://blog.example.com)"
        )
        assert "\u27e81\u27e9" in md
        assert "\u27e82\u27e9" in md
        assert "https://example.com" in refs
        assert "https://blog.example.com" in refs

    def test_link_with_title(self):
        md, refs = convert_to_citations('[Example](https://example.com "Title Text")')
        assert "Title Text" in refs


class TestFastURLJoin:
    def test_absolute_url(self):
        assert (
            fast_urljoin("https://base.com", "https://other.com/page")
            == "https://other.com/page"
        )

    def test_relative_path(self):
        result = fast_urljoin("https://base.com/path/", "/other")
        assert result == "https://base.com/path/other"

    def test_mailto(self):
        assert (
            fast_urljoin("https://base.com", "mailto:test@example.com")
            == "mailto:test@example.com"
        )

    def test_protocol_relative_url(self):
        assert fast_urljoin("https://base.com", "//cdn.example.com/js/app.js") == "//cdn.example.com/js/app.js"
