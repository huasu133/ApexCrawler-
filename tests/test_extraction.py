"""Tests for the extraction module."""
import pytest
from apexcrawler.extraction.ai_extractor import AIExtractor


class TestStructuredExtraction:
    """Test extract_structured method."""

    def setup_method(self):
        self.extractor = AIExtractor()

    def test_extract_json_ld(self):
        """Extract data from JSON-LD."""
        html = """<html><head>
<script type="application/ld+json">
{"name": "Test Product", "description": "A great product", "price": "29.99"}
</script>
</head><body><h1>Test</h1></body></html>"""
        result = self.extractor.extract_structured(html)
        assert result.get("name") == "Test Product"
        assert result.get("description") == "A great product"

    def test_extract_opengraph(self):
        """Extract OpenGraph meta tags."""
        html = """<html><head>
<meta property="og:title" content="OG Title">
<meta property="og:description" content="OG Description">
</head><body></body></html>"""
        result = self.extractor.extract_structured(html)
        assert result.get("og_title") == "OG Title"
        assert result.get("og_description") == "OG Description"

    def test_extract_meta_tags(self):
        """Extract standard meta tags."""
        html = """<html><head>
<meta name="description" content="Meta Description">
<meta name="keywords" content="kw1, kw2">
</head><body></body></html>"""
        result = self.extractor.extract_structured(html)
        assert result.get("description") == "Meta Description"

    def test_extract_empty_html(self):
        """Empty HTML returns empty dict."""
        result = self.extractor.extract_structured("")
        assert result == {}

    def test_extract_no_structured_data(self):
        """HTML without structured data returns empty dict."""
        html = "<html><body><p>Hello</p></body></html>"
        result = self.extractor.extract_structured(html)
        assert result == {}


class TestSmartHTMLTruncate:
    def setup_method(self):
        self.extractor = AIExtractor()

    def test_preserves_json_ld(self):
        """JSON-LD blocks are preserved in truncated output."""
        html = """<html><head>
<script type="application/ld+json">{"name": "Test"}</script>
</head><body><p>Some content here</p></body></html>"""
        result = self.extractor.smart_html_truncate(html)
        assert "application/ld+json" in result

    def test_preserves_main_content(self):
        """Main content area is preserved."""
        html = "<html><body><main><p>Important content</p></main></body></html>"
        result = self.extractor.smart_html_truncate(html)
        assert "Important content" in result

    def test_max_chars_honored(self):
        """Truncation respects character limit."""
        html = "<html><body>" + "<p>content</p>" * 1000 + "</body></html>"
        result = self.extractor.smart_html_truncate(html, max_chars=100)
        assert len(result) <= 100


class TestEnginePool:
    """Test the EnginePool can be imported and instantiated."""

    def test_engine_pool_import(self):
        from apexcrawler.engines.pool import EnginePool
        pool = EnginePool()
        assert pool is not None


class TestDegradeChain:
    def test_degrade_chain_no_httpx(self):
        """Degrade chain should not contain httpx engine."""
        from apexcrawler.pipeline.degrade import DegradeManager
        dm = DegradeManager()
        result = dm.degrade("")
        assert result != "httpx"
        assert result == "vanilla"
