"""Difficulty weighter — determines target difficulty level for routing.

Classifies target websites into difficulty tiers based on known anti-bot
signatures, CDN/WAF presence, and historical crawl data.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DifficultyProfile:
    """Profile describing the anti-bot landscape of a target."""

    score: int  # 1-10 difficulty score
    tier: str  # "low", "medium", "high", "extreme"
    detected_waf: str = ""
    detected_protections: list[str] = field(default_factory=list)
    recommended_engine: str = ""
    notes: str = ""


# Known anti-bot signatures mapped to difficulty contributions
_SIGNATURE_DIFFICULTY: dict[str, int] = {
    "cloudflare": 5,
    "cloudflare_turnstile": 8,
    "datadome": 7,
    "akamai": 7,
    "akamai_bmp": 9,
    "imperva": 6,
    "imperva_utc": 7,
    "distil": 6,
    "f5_shape": 6,
    "perimeterx": 7,
    "humansecurity": 6,
    "forter": 5,
    "kasada": 8,
    "castle": 4,
    "geetest": 5,
    "recaptcha_v2": 4,
    "recaptcha_v3": 6,
    "hcaptcha": 6,
    "funcaptcha": 5,
    "pow_captcha": 7,
    "fingerprintjs": 3,
    "fingerprintjs_pro": 5,
    "creepjs": 4,
    "bloom_filter": 3,
    "canvas_fingerprint": 3,
    "webgl_fingerprint": 3,
    "font_enumeration": 3,
    "wasm_challenge": 6,
    "js_crypto_challenge": 7,
    "header_inspection": 2,
    "cookies_required": 2,
}


def classify_difficulty(
    url: str,
    headers: dict[str, str] | None = None,
    html_snippet: str = "",
    detected_signals: list[str] | None = None,
) -> DifficultyProfile:
    """Classify the difficulty of scraping a target URL.

    Analyzes the URL, response headers, HTML content, and pre-detected
    anti-bot signals to estimate the difficulty level.

    Args:
        url: Target URL.
        headers: Response HTTP headers.
        html_snippet: Page HTML (first few KB is sufficient).
        detected_signals: Pre-identified anti-bot signals.

    Returns:
        A DifficultyProfile with score, tier, and recommendations.
    """
    signals = set(detected_signals or [])
    signals |= _detect_from_url(url)
    if headers:
        signals |= _detect_from_headers(headers)
    if html_snippet:
        signals |= _detect_from_html(html_snippet)

    score, tier = _compute_difficulty(signals)

    return DifficultyProfile(
        score=score,
        tier=tier,
        detected_waf=_identify_waf(signals),
        detected_protections=sorted(signals),
        recommended_engine=_recommend_engine(score),
        notes=_build_notes(score, tier, signals),
    )


class DifficultyWeighter:
    """Weighter that translates anti-bot signals into difficulty scores.

    Used by the routing layer to determine which engine tier to select
    and how to configure evasion parameters.
    """

    # Difficulty tier thresholds
    TIERS: dict[str, tuple[int, int]] = {
        "low": (1, 3),
        "medium": (4, 6),
        "high": (7, 8),
        "extreme": (9, 10),
    }

    # Engine recommendations by tier
    TIER_ENGINES: dict[str, str] = {
        "low": "vanilla",
        "medium": "patched",
        "high": "camoufox",
        "extreme": "cloaked",
    }

    def __init__(self) -> None:
        self._url_cache: dict[str, DifficultyProfile] = {}

    def weigh(self, url: str) -> int:
        """Return a difficulty score (1-10) for a target URL.

        Uses cached results if the URL has been previously classified.
        """
        if url in self._url_cache:
            return self._url_cache[url].score

        profile = classify_difficulty(url)
        self._url_cache[url] = profile
        return profile.score

    def profile(self, url: str) -> DifficultyProfile:
        """Return full difficulty profile for a target URL."""
        if url in self._url_cache:
            return self._url_cache[url]

        profile = classify_difficulty(url)
        self._url_cache[url] = profile
        return profile

    def clear_cache(self) -> None:
        """Clear the URL classification cache."""
        self._url_cache.clear()


# ── Detection helpers ─────────────────────────────────────────

def _detect_from_url(url: str) -> set[str]:
    """Detect anti-bot signals from URL patterns."""
    signals: set[str] = set()
    url_lower = url.lower()

    # Domain-based signals
    if "cloudflare" in url_lower:
        signals.add("cloudflare")
    if "challenges.cloudflare" in url_lower:
        signals.add("cloudflare_turnstile")
    if "datadome" in url_lower:
        signals.add("datadome")
    if "akamai" in url_lower:
        signals.add("akamai")
    if "perimeterx" in url_lower:
        signals.add("perimeterx")
    if "recaptcha" in url_lower or "grecaptcha" in url_lower:
        signals.add("recaptcha_v2")
    if "hcaptcha" in url_lower:
        signals.add("hcaptcha")

    # Path-based signals
    if "/cdn-cgi/" in url_lower:
        signals.add("cloudflare")
    if "/__captcha" in url_lower:
        signals.add("captcha")

    return signals


def _detect_from_headers(headers: dict[str, str]) -> set[str]:
    """Detect anti-bot signals from HTTP response headers."""
    signals: set[str] = set()
    headers_lower = {k.lower(): v for k, v in headers.items()}

    if "cf-ray" in headers_lower or "cf-cache-status" in headers_lower:
        signals.add("cloudflare")
    if "x-datadome" in headers_lower:
        signals.add("datadome")
    if "x-akamai" in headers_lower:
        signals.add("akamai")
    if "x-distil" in headers_lower:
        signals.add("distil")
    if "set-cookie" in headers_lower:
        cookie = headers_lower["set-cookie"]
        if "_cf_" in cookie:
            signals.add("cloudflare")
        if "ak_bmsc" in cookie:
            signals.add("akamai")
        if "reese84" in cookie:
            signals.add("perimeterx")
    if "server" in headers_lower:
        server = headers_lower["server"].lower()
        if "cloudflare" in server:
            signals.add("cloudflare")
        if "akamai" in server:
            signals.add("akamai")

    return signals


def _detect_from_html(html: str) -> set[str]:
    """Detect anti-bot signals from HTML page content."""
    signals: set[str] = set()
    html_lower = html.lower()

    # CAPTCHA indicators
    if "recaptcha/api.js" in html_lower or "grecaptcha" in html_lower:
        signals.add("recaptcha_v2")
    if "hcaptcha.com" in html_lower or "data-hcaptcha" in html_lower:
        signals.add("hcaptcha")
    if "funcaptcha" in html_lower:
        signals.add("funcaptcha")
    if "geetest" in html_lower:
        signals.add("geetest")

    # Challenge pages
    if "challenge-platform" in html_lower:
        signals.add("cloudflare")
    if "_cf_chl_opt" in html_lower or "cf-challenge" in html_lower:
        signals.add("cloudflare_turnstile")
    if "datadome" in html_lower:
        signals.add("datadome")
    if "akamai" in html_lower and "bmp" in html_lower:
        signals.add("akamai_bmp")

    # Fingerprinting scripts
    if "fingerprintjs" in html_lower:
        signals.add("fingerprintjs_pro" if "pro" in html_lower else "fingerprintjs")
    if "creepjs" in html_lower:
        signals.add("creepjs")

    # WASM-based challenges
    if 'wasm' in html_lower and ('challenge' in html_lower or 'verify' in html_lower):
        signals.add("wasm_challenge")

    # JS crypto challenges
    if "pow(" in html_lower or "sha256(" in html_lower or "proofofwork" in html_lower:
        signals.add("pow_captcha")

    return signals


def _compute_difficulty(signals: set[str]) -> tuple[int, str]:
    """Compute difficulty score and tier from a set of detected signals."""
    if not signals:
        return 1, "low"

    # Sum up difficulty contributions from known signatures
    total = 0
    for sig in signals:
        total += _SIGNATURE_DIFFICULTY.get(sig, 0)

    # Cap at base difficulty 5 if no known signatures matched
    if total == 0:
        total = 2

    # Score is max contribution + bonus for multiple signals
    max_sig = max((_SIGNATURE_DIFFICULTY.get(s, 0) for s in signals), default=0)
    bonus = min(len(signals) - 1, 3)  # +1 per additional signal, max +3
    score = min(max_sig + bonus, 10)
    score = max(score, 1)

    # Map to tier
    if score <= 3:
        tier = "low"
    elif score <= 6:
        tier = "medium"
    elif score <= 8:
        tier = "high"
    else:
        tier = "extreme"

    return score, tier


def _identify_waf(signals: set[str]) -> str:
    """Identify the primary WAF/CDN from detected signals."""
    waf_signals = {
        "cloudflare": "Cloudflare",
        "cloudflare_turnstile": "Cloudflare Turnstile",
        "datadome": "DataDome",
        "akamai": "Akamai",
        "akamai_bmp": "Akamai BMP",
        "imperva": "Imperva",
        "imperva_utc": "Imperva UTC",
        "distil": "Distil Networks",
        "perimeterx": "PerimeterX / HUMAN",
        "f5_shape": "F5 Shape Security",
        "humansecurity": "HUMAN Security",
        "kasada": "Kasada",
    }
    for sig, name in waf_signals.items():
        if sig in signals:
            return name
    return ""


def _recommend_engine(score: int) -> str:
    """Recommend the best engine tier for a given difficulty score."""
    if score <= 3:
        return "vanilla"
    elif score <= 6:
        return "patched"
    elif score <= 8:
        return "camoufox"
    else:
        return "cloaked"


def _build_notes(score: int, tier: str, signals: set[str]) -> str:
    """Build human-readable notes about the difficulty classification."""
    parts: list[str] = []

    if signals:
        parts.append(f"Detected {len(signals)} anti-bot signal(s)")

    if tier == "low":
        parts.append("Standard requests should succeed without stealth")
    elif tier == "medium":
        parts.append("DOM patching recommended; monitor for rate limits")
    elif tier == "high":
        parts.append("Full fingerprint camouflage required; use proxy rotation")
    elif tier == "extreme":
        parts.append(
            "Maximum stealth with CDP hiding, WASM interception, "
            "and aggressive proxy rotation needed"
        )

    return "; ".join(parts)
