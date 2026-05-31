"""TLS fingerprint router with JA4 profile management."""

from dataclasses import dataclass


@dataclass
class TLSProfile:
    name: str           # "chrome_124"
    ua: str             # Full User-Agent string
    ja4_prefix: str     # "t13d1516h2"
    alpn: list[str]     # ["h2", "http/1.1"]
    platform: str       # "Windows"
    accept_language: str  # "en-US,en;q=0.9"


# Pre-configured profiles
PROFILES = {
    "chrome_124": TLSProfile(
        name="chrome_124",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ja4_prefix="t13d1516h2",
        alpn=["h2", "http/1.1"],
        platform="Windows",
        accept_language="en-US,en;q=0.9",
    ),
    "chrome_131": TLSProfile(
        name="chrome_131",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        ja4_prefix="t13d1616h2",
        alpn=["h2", "http/1.1"],
        platform="Windows",
        accept_language="en-US,en;q=0.9",
    ),
    "firefox_124": TLSProfile(
        name="firefox_124",
        ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
        ja4_prefix="t13d1715h2",
        alpn=["h2", "http/1.1"],
        platform="Windows",
        accept_language="en-US,en;q=0.9",
    ),
}


class TLSRouter:
    """Selects and manages TLS profiles with rotation."""

    def __init__(self, profiles: dict[str, TLSProfile] | None = None):
        self._profiles = profiles or PROFILES
        self._idx = 0

    def get(self, name: str | None = None) -> TLSProfile:
        if name and name in self._profiles:
            return self._profiles[name]
        keys = list(self._profiles.keys())
        return self._profiles[keys[self._idx % len(keys)]]

    def rotate(self) -> TLSProfile:
        keys = list(self._profiles.keys())
        profile = self._profiles[keys[self._idx % len(keys)]]
        self._idx += 1
        return profile

    def validate_consistency(self, profile: TLSProfile) -> bool:
        """Check TLS/UA/ALPN consistency."""
        # UA says Chrome → ALPN must include h2
        if "Chrome" in profile.ua and "h2" not in profile.alpn:
            return False
        return True
