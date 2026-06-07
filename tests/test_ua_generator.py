"""Tests for UA generator module."""

import pytest
from apexcrawler.fingerprint.ua_generator import UAGenerator, UAResult


class TestUAGenerator:
    def test_generate_chrome_win(self):
        uag = UAGenerator()
        result = uag.generate("chrome", "win")
        assert "Mozilla" in result.ua
        assert "Chrome" in result.ua
        assert "Windows" in result.ua

    def test_generate_firefox(self):
        uag = UAGenerator()
        result = uag.generate("firefox", "mac")
        assert "Firefox" in result.ua

    def test_generate_edge(self):
        uag = UAGenerator()
        result = uag.generate("edge", "win")
        assert "Edg" in result.ua
        assert "Chrome" in result.ua

    def test_generate_client_hints_chrome(self):
        hints = UAGenerator.generate_client_hints(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
        )
        assert "Chromium" in hints
        assert "Google Chrome" in hints

    def test_random_returns_different_values(self):
        uag = UAGenerator()
        results = set()
        for _ in range(5):
            result = uag.generate()
            results.add(result.ua)
        # At least some should differ
        assert len(results) > 1

    def test_uar_result_has_all_fields(self):
        uag = UAGenerator()
        result = uag.generate("chrome", "mac")
        assert hasattr(result, "ua")
        assert hasattr(result, "sec_ch_ua")
        assert hasattr(result, "sec_ch_ua_platform")
        assert hasattr(result, "sec_ch_ua_mobile")
        assert hasattr(result, "platform")
        assert "Macintosh" in result.ua

    def test_generate_edge_has_client_hints(self):
        uag = UAGenerator()
        result = uag.generate("edge", "win", version=124)
        assert "Edg" in result.ua
        assert result.sec_ch_ua is not None

    def test_generate_safari(self):
        uag = UAGenerator()
        result = uag.generate("safari", "mac")
        assert "Safari" in result.ua
        assert "Version/" in result.ua

    def test_generate_firefox_client_hints_empty(self):
        uag = UAGenerator()
        result = uag.generate("firefox", "win", version=124)
        assert result.sec_ch_ua == '""'

    def test_generate_unknown_browser_falls_back(self):
        uag = UAGenerator()
        result = uag.generate("unknown_browser", "linux")
        assert "Chrome" in result.ua
        assert result.sec_ch_ua is not None

    def test_uar_result_type(self):
        uag = UAGenerator()
        result = uag.generate()
        assert isinstance(result, UAResult)

    def test_generate_client_hints_edge(self):
        hints = UAGenerator.generate_client_hints(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        )
        assert "Microsoft Edge" in hints
        assert "Chromium" in hints
