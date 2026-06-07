"""Tests for fingerprint consistency module."""

import pytest
from apexcrawler.fingerprint.consistency import DeviceProfile, get_profile


class TestDeviceProfile:
    def test_get_profile_default(self):
        """get_profile() returns default profile."""
        profile = get_profile("win_chrome_124")
        assert profile is not None
        assert "Chrome" in profile.user_agent

    def test_get_profile_cn(self):
        """cn_win_chrome_120 profile has Chinese settings."""
        profile = get_profile("cn_win_chrome_120")
        assert profile.language == "zh-CN"
        assert profile.timezone == "Asia/Shanghai"

    def test_cdp_inject_script_length(self):
        """cdp_inject_script() returns valid JS."""
        profile = get_profile()
        script = profile.cdp_inject_script()
        assert len(script) > 1000
        assert "navigator" in script

    def test_cdp_inject_has_fixes(self):
        """Injected script contains critical fingerprint fixes."""
        profile = get_profile()
        script = profile.cdp_inject_script()
        assert "languages" in script
        assert "plugins" in script
        assert "webdriver" in script

    def test_get_profile_nonexistent_falls_back(self):
        """Non-existent profile name falls back to random."""
        profile = get_profile("nonexistent_profile_xyz")
        assert profile is not None
        assert isinstance(profile, DeviceProfile)

    def test_profile_validation_ok(self):
        """Default profile passes validation."""
        profile = get_profile("win_chrome_124")
        errors = profile.validate()
        assert errors == []
