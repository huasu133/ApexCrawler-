"""Anti-crawl signal detectors.

Detects anti-crawl signals in HTTP responses and HTML content:
- CAPTCHA patterns (reCAPTCHA, hCaptcha, Cloudflare Turnstile, etc.)
- JavaScript challenges
- WAF blocks (Cloudflare, Akamai, DataDome)
- Rate limit indicators
- Honey-pot traps
- Browser fingerprinting scripts

Each detector returns a SignalResult with detection confidence (0-1).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    """Result from a single signal detector."""
    detected: bool
    signal_type: str           # "captcha", "challenge", "block", "rate_limit", "fingerprint"
    vendor: str = ""           # "cloudflare", "recaptcha", "hcaptcha", etc.
    confidence: float = 1.0    # 0.0–1.0
    evidence: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


# ── CAPTCHA Detectors ──────────────────────────────────────

_CAPTCHA_PATTERNS = {
    "recaptcha": [
        r'//www\.google\.com/recaptcha/api\.js',
        r'//www\.google\.com/recaptcha/enterprise\.js',
        r'g-recaptcha',
        r'data-sitekey',
        r'grecaptcha\.',
        r'__recaptcha_api',
    ],
    "hcaptcha": [
        r'//hcaptcha\.com/1/api\.js',
        r'\.hcaptcha\.com',
        r'data-hcaptcha',
        r'hcaptcha\.render',
    ],
    "cloudflare_turnstile": [
        r'//challenges\.cloudflare\.com/turnstile',
        r'data-turnstile',
        r'cf-turnstile',
        r'turnstile\.render',
    ],
    "geetest": [
        r'//static\.geetest\.com',
        r'initGeetest',
        r'geetest\.',
    ],
    "arkose": [
        r'//.*\.funcaptcha\.com',
        r'funcaptcha',
        r'arkoselabs',
        r'arkose-enforcement',
    ],
    "generic_captcha": [
        r'captcha',
        r'verify.?(?:you.?are|that.?you.?are).?human',
        r'are you a (?:human|robot)',
        r'prove you[^<]{0,30}(?:human|not a robot)',
    ],
}


class CaptchaDetector:
    """Detects CAPTCHA challenges in HTML/headers."""

    def detect(self, html: str, headers: dict[str, str]) -> SignalResult:
        """Check for CAPTCHA presence.

        Args:
            html: Page HTML content.
            headers: HTTP response headers.

        Returns:
            SignalResult with detection details.
        """
        # Use case-insensitive matching with explicit IGNORECASE flags
        for vendor, patterns in _CAPTCHA_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, html, re.IGNORECASE):
                    return SignalResult(
                        detected=True,
                        signal_type="captcha",
                        vendor=vendor,
                        confidence=0.95 if vendor != "generic_captcha" else 0.7,
                        evidence=[f"HTML match: {pattern}"]
                    )

        # Check for common CAPTCHA status codes
        if str(headers.get("x-captcha", "")).lower() == "true":
            return SignalResult(
                detected=True,
                signal_type="captcha",
                vendor="generic_captcha",
                confidence=0.9,
                evidence=["x-captcha header detected"]
            )

        return SignalResult(detected=False, signal_type="captcha", confidence=0.0)


# ── WAF / Block Detectors ──────────────────────────────────

_WAF_PATTERNS = {
    "cloudflare": {
        "headers": ["cf-ray", "cf-chl-out", "cf-chl-bypass", "cf-mitigated"],
        "cookies": ["cf_clearance", "__cf_bm", "__cfduid"],
        "html": [
            r'Checking your browser before accessing',
            r'cf-browser-verification',
            r'/cdn-cgi/challenge-platform',
            r'DDoS protection by Cloudflare',
            r'cloudflare-ip-country',
        ],
    },
    "akamai": {
        "cookies": ["_abck", "ak_bmsc", "bm_sz", "bm_mi", "bm_sv"],
        "html": [
            r'akamai-intercept',
            r'bm-bot-detection',
        ],
    },
    "datadome": {
        "headers": ["x-datadome"],
        "cookies": ["datadome"],
        "html": [r'datadome-client', r'datadome'],
    },
    "perimeterx": {
        "cookies": ["_px3", "_pxde", "_pxhd", "_pxff"],
        "html": [r'human\.perimeterx\.net', r'_pxCaptcha'],
    },
    "imperva": {
        "cookies": ["incap_ses_", "visid_incap_"],
        "html": [r'incapsula', r'incap_ses', r'_Incapsula_Resource'],
    },
    "distil": {
        "cookies": ["distil"],
        "html": [r'distil', r'distil_'],
    },
}


class WAFDetector:
    """Detects Web Application Firewall (WAF) blocks and challenges."""

    def detect(self, html: str, headers: dict[str, str], status: int) -> SignalResult:
        """Check for WAF presence in headers, cookies, and HTML.

        Args:
            html: Page HTML content.
            headers: HTTP response headers (as dict with lowercased keys).
            status: HTTP status code.

        Returns:
            SignalResult with detection details.
        """
        evidence: list[str] = []
        header_keys_lower = {k.lower() for k in headers}

        # Status code heuristics
        if status == 403:
            evidence.append(f"HTTP 403 Forbidden")
        elif status == 429:
            return SignalResult(
                detected=True,
                signal_type="rate_limit",
                vendor="generic",
                confidence=0.95,
                evidence=[f"HTTP 429 Too Many Requests"],
            )

        for vendor, sigs in _WAF_PATTERNS.items():
            vendor_evidence: list[str] = []

            # Check headers
            for h in sigs.get("headers", []):
                for actual_h in header_keys_lower:
                    if h.lower() == actual_h:
                        vendor_evidence.append(f"Header: {actual_h}={headers.get(actual_h, '')}")

            # Check cookies (from headers)
            cookie_header = headers.get("set-cookie", "")
            for c in sigs.get("cookies", []):
                if c.lower() in cookie_header.lower():
                    vendor_evidence.append(f"Cookie: {c}")

            # Check HTML patterns
            for pattern in sigs.get("html", []):
                if re.search(pattern, html, re.IGNORECASE):
                    vendor_evidence.append(f"HTML match: {pattern}")

            if vendor_evidence:
                all_evidence = evidence + vendor_evidence
                return SignalResult(
                    detected=True,
                    signal_type="block" if status == 403 else "challenge",
                    vendor=vendor,
                    confidence=0.9,
                    evidence=all_evidence,
                )

        return SignalResult(detected=False, signal_type="block", confidence=0.0)


# ── JavaScript Challenge Detector ──────────────────────────

_JS_CHALLENGE_PATTERNS = [
    r'window\.location\.(?:reload|replace)',
    r'document\.cookie\s*=\s*["\'][^"\']*[=;]',
    r'eval\s*\(\s*function\s*\(',
    r'atob\s*\(\s*[\'"]',
    r'String\.fromCharCode\s*\(',
    r'unescape\s*\(\s*[\'"]',
    r'__jsl_clearance',
    r'challenge-platform',
    r'jschl-answer',
    r'submit.*challenge',
]


class JSChallengeDetector:
    """Detects JavaScript-based challenges (Cloudflare "I'm Under Attack", etc.)."""

    def detect(self, html: str, headers: dict[str, str]) -> SignalResult:
        """Check for JavaScript challenge patterns.

        Args:
            html: Page HTML content.
            headers: HTTP response headers.

        Returns:
            SignalResult with detection details.
        """
        evidence: list[str] = []
        for pattern in _JS_CHALLENGE_PATTERNS:
            if re.search(pattern, html, re.IGNORECASE):
                evidence.append(pattern)

        if evidence:
            return SignalResult(
                detected=True,
                signal_type="challenge",
                vendor="generic_js",
                confidence=min(1.0, 0.5 + 0.15 * len(evidence)),
                evidence=evidence,
            )

        # Check for 503 + cf-ray combo (Cloudflare challenge mode)
        hdr_keys = {k.lower() for k in headers}
        if "cf-ray" in hdr_keys and "cf-mitigated" in hdr_keys:
            return SignalResult(
                detected=True,
                signal_type="challenge",
                vendor="cloudflare",
                confidence=0.85,
                evidence=["cf-ray + cf-mitigated headers"]
            )

        return SignalResult(detected=False, signal_type="challenge", confidence=0.0)


# ── Fingerprinting Detector ────────────────────────────────

_FINGERPRINT_PATTERNS = [
    r'navigator\.(?:webdriver|userAgent|platform|language|languages|plugins|mimeTypes|hardwareConcurrency|deviceMemory)',
    r'canvas\.toDataURL|canvas\.getImageData',
    r'WebGLRenderingContext|WEBGL_debug_renderer_info',
    r'AudioContext|OscillatorNode',
    r'RTCPeerConnection|webdriver|__webdriver',
    r'fingerprint(?:js|2)',
    r'fp-promise|src/fp\.js',
    r'font-detection|fonts\.check|FontFace',
    r'screen\.(?:width|height|colorDepth|pixelDepth|avail)',
    r'performance\.(?:timing|navigation|getEntries)',
    r'Date\.prototype\.getTimezoneOffset',
]


class FingerprintDetector:
    """Detects browser fingerprinting scripts."""

    def detect(self, html: str, headers: dict[str, str]) -> SignalResult:
        """Check for fingerprinting scripts.

        Args:
            html: Page HTML content.
            headers: HTTP response headers.

        Returns:
            SignalResult with detection details.
        """
        evidence: list[str] = []
        for pattern in _FINGERPRINT_PATTERNS:
            if re.search(pattern, html, re.IGNORECASE):
                evidence.append(pattern)

        if not evidence:
            return SignalResult(detected=False, signal_type="fingerprint", confidence=0.0)

        score = min(1.0, 0.3 + 0.1 * len(evidence))
        return SignalResult(
            detected=score > 0.5,
            signal_type="fingerprint",
            vendor="generic",
            confidence=score,
            evidence=evidence,
        )


# ── Honeypot Detector ──────────────────────────────────────

_HONEYPOT_PATTERNS = [
    (r'<(?:input|textarea)[^>]*name\s*=\s*["\'][^"\']*(?:email|phone|url|comment|name|first|last)["\'][^>]*style\s*=\s*["\'].*?(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|position\s*:\s*absolute.*?(?:left|top)\s*:\s*-9999)', "hidden input field"),
    (r'<input[^>]*type\s*=\s*["\']hidden["\'][^>]*name\s*=\s*["\'][^"\']*(?:csrf|token|key|id)["\']', "CSRF token field"),
    (r'<a[^>]*href\s*=\s*["\'][^"\']*["\'][^>]*style\s*=\s*["\'].*?display\s*:\s*none', "hidden link"),
]


class HoneypotDetector:
    """Detects honeypot traps designed to catch automated crawlers."""

    def detect(self, html: str, headers: dict[str, str]) -> SignalResult:
        """Check for honeypot elements in HTML.

        Args:
            html: Page HTML content.
            headers: HTTP response headers.

        Returns:
            SignalResult with detection details.
        """
        evidence: list[str] = []
        for pattern, desc in _HONEYPOT_PATTERNS:
            if re.search(pattern, html, re.IGNORECASE | re.DOTALL):
                evidence.append(desc)

        if evidence:
            return SignalResult(
                detected=True,
                signal_type="honeypot",
                vendor="generic",
                confidence=0.8,
                evidence=evidence,
            )

        return SignalResult(detected=False, signal_type="honeypot", confidence=0.0)


# ── Composite Detector ─────────────────────────────────────

class SignalDetector:
    """Runs all detectors and returns a consolidated analysis.

    Usage:
        detector = SignalDetector()
        results = detector.analyze(html, headers, status=200)
        for r in results:
            if r.detected:
                print(f"Found: {r.signal_type} ({r.vendor}), confidence={r.confidence}")
    """

    def __init__(self, detectors: list | None = None):
        self._detectors = detectors or [
            CaptchaDetector(),
            WAFDetector(),
            JSChallengeDetector(),
            FingerprintDetector(),
            HoneypotDetector(),
        ]

    def analyze(
        self, html: str, headers: dict[str, str], status: int = 200
    ) -> list[SignalResult]:
        """Run all detectors and classify the response.

        Args:
            html: Page HTML content.
            headers: HTTP response headers.
            status: HTTP status code.

        Returns:
            List of SignalResult from each detector (including negatives).
        """
        results: list[SignalResult] = []
        for detector in self._detectors:
            try:
                if isinstance(detector, WAFDetector):
                    result = detector.detect(html, headers, status)
                else:
                    result = detector.detect(html, headers)  # type: ignore
                results.append(result)
            except Exception as e:
                logger.warning(f"Detector {type(detector).__name__} failed: {e}")
                results.append(SignalResult(
                    detected=False,
                    signal_type="error",
                    evidence=[str(e)],
                ))

        return results

    @property
    def active_threats(self) -> list[SignalResult]:
        """Convenience accessor. Call analyze() first, then check this."""
        return []  # Results are returned from analyze()

    def highest_confidence(self, results: list[SignalResult]) -> SignalResult | None:
        """Return the detected signal with the highest confidence."""
        detected = [r for r in results if r.detected]
        if not detected:
            return None
        return max(detected, key=lambda r: r.confidence)

    def is_blocked(self, results: list[SignalResult]) -> bool:
        """Check if any result indicates blocking/challenging."""
        blocking_types = {"block", "captcha", "challenge", "rate_limit"}
        return any(r.detected and r.signal_type in blocking_types for r in results)


# ── Cloudflare Dedicated Detector ────────────────────────────

_CLOUDFLARE_PATTERNS = {
    "headers": [
        "cf-ray",
        "cf-chl-out",
        "cf-chl-bypass",
        "cf-mitigated",
        "cf-request-id",
    ],
    "html_title": [
        r"Just a moment\.\.\.",
        r"Checking your browser",
        r"Attention Required! \| Cloudflare",
        r"Please complete the security check to access",
    ],
    "html_body": [
        r"__cf_chl_tk",
        r"cf-challenge",
        r"cf-browser-verification",
        r"/cdn-cgi/challenge-platform/",
        r"DDoS protection by Cloudflare",
        r"cloudflare-ip-country",
        r"cf-error-detect-by",
        r"_cf_chl_opt",
        r"cpo\.src\s*=\s*[\x22\x27]/cdn-cgi/challenge-platform",
        r"challenge-platform.*turnstile",
    ],
    "url_fragment": [
        r"/cdn-cgi/challenge-platform/",
        r"__cf_chl_f_tk",
        r"__cf_chl_captcha_tk",
    ],
}


class CloudflareDetector:
    """
    检测 Cloudflare 保护的页面。

    检测特征:
    - 响应状态码 403 + cf-ray header
    - 响应体包含 "__cf_chl_tk" 或 "cf-challenge"
    - 响应体包含 "Just a moment..." 或 "Checking your browser"
    - 响应体包含 "Attention Required! | Cloudflare"
    - 页面 URL 包含 "/cdn-cgi/challenge-platform/"

    检测到后返回建议的引擎: "cloaked" (最强反 CF 引擎)
    """

    RECOMMENDED_ENGINE = "cloaked"

    @classmethod
    def recommended_engine(cls) -> str:
        """Return the recommended engine for Cloudflare-protected pages."""
        return cls.RECOMMENDED_ENGINE

    def detect(self, html: str, headers: dict[str, str], status: int = 200,
               url: str = "") -> SignalResult:
        """Detect Cloudflare protection signals.

        Args:
            html: Page HTML content.
            headers: HTTP response headers (as dict with lowercased keys).
            status: HTTP status code.
            url: The page URL (for URL fragment checks).

        Returns:
            SignalResult with detection details, including 'recommended_engine'
            in meta if Cloudflare is detected.
        """
        evidence: list[str] = []
        header_keys_lower = {k.lower() for k in headers}

        # 1. Status code heuristics
        if status == 403:
            evidence.append(f"HTTP 403 Forbidden")
        elif status == 503:
            evidence.append(f"HTTP 503 Service Unavailable (CF challenge mode)")

        # 2. Header-based detection
        for h in _CLOUDFLARE_PATTERNS["headers"]:
            if h.lower() in header_keys_lower:
                evidence.append(f"Header: {h}={headers.get(h, '')}")

        # 3. HTML title detection
        for pattern in _CLOUDFLARE_PATTERNS["html_title"]:
            if re.search(pattern, html, re.IGNORECASE):
                evidence.append(f"HTML title match: {pattern}")

        # 4. HTML body pattern detection
        for pattern in _CLOUDFLARE_PATTERNS["html_body"]:
            if re.search(pattern, html, re.IGNORECASE):
                evidence.append(f"HTML body match: {pattern}")

        # 5. URL fragment detection
        if url:
            for pattern in _CLOUDFLARE_PATTERNS["url_fragment"]:
                if re.search(pattern, url, re.IGNORECASE):
                    evidence.append(f"URL match: {pattern}")

        if not evidence:
            return SignalResult(detected=False, signal_type="block", confidence=0.0)

        # Calculate confidence based on evidence strength
        confidence = 0.0
        has_status_evidence = any("HTTP" in e for e in evidence)
        has_header_evidence = any(e.startswith("Header: cf-") for e in evidence)
        has_body_evidence = any(e.startswith("HTML body match") or e.startswith("HTML title match") for e in evidence)

        if has_status_evidence and has_header_evidence:
            confidence = 1.0  # Definite CF block
        elif has_header_evidence and has_body_evidence:
            confidence = 0.95
        elif has_header_evidence:
            confidence = 0.85
        elif has_body_evidence:
            confidence = 0.80
        else:
            confidence = 0.70

        return SignalResult(
            detected=True,
            signal_type="block",
            vendor="cloudflare",
            confidence=confidence,
            evidence=evidence,
            meta={
                "recommended_engine": self.RECOMMENDED_ENGINE,
                "challenge_type": self._detect_challenge_type(html),
            },
        )

    @staticmethod
    def _detect_challenge_type(html: str) -> str:
        """Detect the specific Cloudflare challenge type.

        Returns one of: "interactive", "non_interactive", "managed", "embedded", "js_challenge", ""
        """
        if "cType: 'non-interactive'" in html:
            return "non_interactive"
        if "cType: 'managed'" in html:
            return "managed"
        if "cType: 'interactive'" in html:
            return "interactive"
        if "challenges.cloudflare.com/turnstile" in html:
            return "embedded"
        if "/cdn-cgi/challenge-platform" in html:
            return "js_challenge"
        return ""

    @staticmethod
    def detect_turnstile_presence(html: str) -> bool:
        """Check if the page contains a Cloudflare Turnstile challenge.

        Detects:
        - iframe[src*="challenges.cloudflare.com"]
        - input[name="cf-turnstile-response"]
        - div.cf-turnstile
        - script[src*="challenges.cloudflare.com/turnstile"]
        """
        patterns = [
            r"<iframe[^>]*src\s*=\s*[\x22\x27]https?://challenges\.cloudflare\.com",
            r"<input[^>]*name\s*=\s*[\x22\x27]cf-turnstile-response[\x22\x27]",
            r"<div[^>]*class\s*=\s*[\x22\x27][^\x22\x27]*cf-turnstile[^\x22\x27]*[\x22\x27]",
            r"src\s*=\s*[\x22\x27]https?://challenges\.cloudflare\.com/turnstile",
            r"data-turnstile",
            r"cf-turnstile-wrapper",
            r"turnstile\.render",
        ]
        for pattern in patterns:
            if re.search(pattern, html, re.IGNORECASE):
                return True
        return False
