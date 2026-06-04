"""TLS fingerprint router with JA4 profile management."""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from types import MappingProxyType


@dataclass
class TLSProfile:
    name: str           # "chrome_124"
    ua: str             # Full User-Agent string
    ja4_prefix: str     # "t13d1516h2"
    alpn: list[str]     # ["h2", "http/1.1"]
    platform: str       # "Windows"
    accept_language: str  # "en-US,en;q=0.9"
    sec_ch_ua: str = ""          # Sec-CH-UA header value
    sec_ch_ua_platform: str = "" # Sec-CH-UA-Platform header value


# Pre-configured profiles
PROFILES = MappingProxyType({
    "chrome_124": TLSProfile(
        name="chrome_124",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ja4_prefix="t13d1516h2",
        alpn=["h2", "http/1.1"],
        platform="Windows",
        accept_language="en-US,en;q=0.9",
        sec_ch_ua='"Google Chrome";v="124", "Chromium";v="124", "Not=A?Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
    ),
    "chrome_131": TLSProfile(
        name="chrome_131",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ja4_prefix="t13d1616h2",
        alpn=["h2", "http/1.1"],
        platform="Windows",
        accept_language="en-US,en;q=0.9",
        sec_ch_ua='"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"',
        sec_ch_ua_platform='"Windows"',
    ),
    "firefox_124": TLSProfile(
        name="firefox_124",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        ja4_prefix="t13d1715h2",
        alpn=["h2", "http/1.1"],
        platform="Windows",
        accept_language="en-US,en;q=0.9",
        sec_ch_ua="",
        sec_ch_ua_platform="",
    ),
})


class TLSRouter:
    """Selects and manages TLS profiles with rotation."""

    def __init__(self, profiles: dict[str, TLSProfile] | None = None):
        self._profiles = profiles or PROFILES
        self._cycle = itertools.cycle(self._profiles.keys())

    def get(self, name: str | None = None) -> TLSProfile:
        if name and name in self._profiles:
            return self._profiles[name]
        key = next(self._cycle)
        return self._profiles[key]

    def rotate(self, random_mode: bool = False) -> TLSProfile:
        keys = list(self._profiles.keys())
        if random_mode:
            profile = self._profiles[random.choice(keys)]
        else:
            key = next(self._cycle)
            profile = self._profiles[key]
        return profile

    def validate_consistency(self, profile: TLSProfile) -> bool:
        """Check TLS/UA/ALPN consistency."""
        # UA says Chrome → ALPN must include h2
        if "Chrome" in profile.ua and "h2" not in profile.alpn:
            return False
        return True
