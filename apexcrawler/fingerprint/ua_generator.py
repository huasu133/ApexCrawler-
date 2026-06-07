"""Dynamic User-Agent generator with Client Hints support.

Generates realistic browser UA strings and corresponding Sec-CH-UA headers.
Inspiration: Crawl4AI's user_agent_generator.py and ua_generator library.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Optional


# Modern browser UA templates
UA_TEMPLATES = {
    "chrome_win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{version}.0.0.0 Safari/537.36"
    ),
    "chrome_mac": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{version}.0.0.0 Safari/537.36"
    ),
    "chrome_linux": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{version}.0.0.0 Safari/537.36"
    ),
    "firefox_win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{version}.0) "
        "Gecko/20100101 Firefox/{version}.0"
    ),
    "firefox_mac": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{version}.0) "
        "Gecko/20100101 Firefox/{version}.0"
    ),
    "edge_win": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/{version}.0.0.0 Safari/537.36 "
        "Edg/{version}.0.0.0"
    ),
    "safari_mac": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/{version}.0 Safari/605.1.15"
    ),
}

# Version ranges for each browser
VERSION_RANGES = {
    "chrome": (120, 135),
    "firefox": (120, 135),
    "edge": (120, 135),
    "safari": (17, 19),
}

# Platform mapping for Sec-CH-UA-Platform header
PLATFORM_MAP = {
    "win": '"Windows"',
    "mac": '"macOS"',
    "linux": '"Linux"',
}


@dataclass
class UAResult:
    """Result of a UA generation, including matching Client Hints headers."""

    ua: str
    sec_ch_ua: str
    sec_ch_ua_platform: str
    sec_ch_ua_mobile: str
    platform: str


def _detect_browser_from_ua(ua: str) -> Optional[str]:
    """Detect which browser a UA string belongs to."""
    if "Edg/" in ua:
        return "edge"
    if "Firefox/" in ua:
        return "firefox"
    if "Chrome/" in ua:
        return "chrome"
    if "Safari/" in ua:
        return "safari"
    if "Version/" in ua:
        return "safari"
    return None


def _extract_version(ua: str, browser: str) -> Optional[str]:
    """Extract browser major version from UA string."""
    pattern_map = {
        "chrome": r"Chrome/(\d+)",
        "edge": r"Edg/(\d+)",
        "firefox": r"Firefox/(\d+)",
        "safari": r"Version/(\d+)",
    }
    pattern = pattern_map.get(browser)
    if not pattern:
        return None
    m = re.search(pattern, ua)
    return m.group(1) if m else None


class UAGenerator:
    """Dynamic User-Agent generator with Client Hints.

    Usage:
        uag = UAGenerator()
        result = uag.generate(browser="chrome", os="win")
        # result.ua = "Mozilla/5.0 (Windows NT 10.0; ... Chrome/131.0.0.0 Safari/537.36"
        # result.sec_ch_ua = '"Chromium";v="131", "Not_A Brand";v="8", "Google Chrome";v="131"'
    """

    def __init__(self) -> None:
        self._current_version: int = 131

    def generate(
        self,
        browser: str = "chrome",
        os: str = "win",
        version: Optional[int] = None,
    ) -> UAResult:
        """Generate a UA string with matching Client Hints.

        Args:
            browser: One of "chrome", "firefox", "edge", "safari".
            os: One of "win", "mac", "linux". Safari only supports "mac".
            version: Explicit browser major version. If None, a random version
                     within the modern range is used.

        Returns:
            UAResult with ua string and matching Client Hints headers.
        """
        template_key = f"{browser}_{os}"
        if template_key not in UA_TEMPLATES:
            # Fall back to chrome_win for unknown combos
            template_key = "chrome_win"
            browser = "chrome"
            os = "win"

        v = version or random.randint(*VERSION_RANGES.get(browser, (120, 135)))
        ua = UA_TEMPLATES[template_key].format(version=v)

        sec_ch_ua = self._build_sec_ch_ua(browser, v)
        sec_ch_ua_platform = PLATFORM_MAP.get(os, '"Windows"')
        sec_ch_ua_mobile = "?0"

        platform_name_map = {"win": "Win32", "mac": "MacIntel", "linux": "Linux x86_64"}
        platform = platform_name_map.get(os, "Win32")

        return UAResult(
            ua=ua,
            sec_ch_ua=sec_ch_ua,
            sec_ch_ua_platform=sec_ch_ua_platform,
            sec_ch_ua_mobile=sec_ch_ua_mobile,
            platform=platform,
        )

    @staticmethod
    def generate_client_hints(ua_string: str) -> str:
        """Generate Sec-CH-UA header value by parsing an existing UA string.

        Args:
            ua_string: A browser user-agent string.

        Returns:
            Sec-CH-UA header value (e.g. '"Chromium";v="131", "Not_A Brand";v="8", ...').
            Returns '""' for browsers that don't typically send Sec-CH-UA (Firefox, Safari).
        """
        browser = _detect_browser_from_ua(ua_string)
        if not browser:
            return '""'

        version = _extract_version(ua_string, browser)
        if not version:
            return '""'

        hints = []

        if browser == "chrome":
            hints.append(f'"Chromium";v="{version}"')
            hints.append('"Not_A Brand";v="8"')
            hints.append(f'"Google Chrome";v="{version}"')

        elif browser == "edge":
            hints.append(f'"Chromium";v="{version}"')
            hints.append('"Not_A Brand";v="8"')
            hints.append(f'"Microsoft Edge";v="{version}"')

        elif browser == "firefox":
            # Firefox doesn't typically send Sec-CH-UA
            return '""'

        elif browser == "safari":
            hints.append(f'"Safari";v="{version}"')
            hints.append('"Not_A Brand";v="8"')

        return ", ".join(hints)

    @staticmethod
    def _build_sec_ch_ua(browser: str, version: int) -> str:
        """Build the Sec-CH-UA header value for a given browser + version."""
        hints = []

        if browser == "chrome":
            hints.append(f'"Chromium";v="{version}"')
            hints.append('"Not_A Brand";v="8"')
            hints.append(f'"Google Chrome";v="{version}"')

        elif browser == "edge":
            hints.append(f'"Chromium";v="{version}"')
            hints.append('"Not_A Brand";v="8"')
            hints.append(f'"Microsoft Edge";v="{version}"')

        elif browser == "firefox":
            return '""'

        elif browser == "safari":
            hints.append(f'"Safari";v="{version}"')
            hints.append('"Not_A Brand";v="8"')

        return ", ".join(hints)

    def random(self) -> UAResult:
        """Generate a random UA with random browser + OS combination."""
        browser = random.choice(list(VERSION_RANGES.keys()))
        valid_os = {"chrome": ["win", "mac", "linux"], "firefox": ["win", "mac"], "edge": ["win"], "safari": ["mac"]}
        os_options = valid_os.get(browser, ["win"])
        os_val = random.choice(os_options)
        return self.generate(browser=browser, os=os_val)
