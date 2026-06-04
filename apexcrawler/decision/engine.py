"""LLM-based adaptive decision engine with three-tier caching."""

from __future__ import annotations
import json, logging, re, time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Anti-crawl vendor signatures
VENDOR_SIGNATURES = {
    "cloudflare": {"headers": ["cf-ray", "cf-chl-out", "cf-chl-bypass"], "cookies": ["cf_clearance", "__cf_bm"]},
    "akamai": {"cookies": ["_abck", "ak_bmsc", "bm_sz"]},
    "datadome": {"cookies": ["datadome"], "headers": ["x-datadome"]},
    "perimeterx": {"cookies": ["_px3", "_pxde", "_pxhd"]},
    "kasada": {"headers": ["x-kpsdk-ct", "x-kpsdk-cd"]},
    "f5_shape": {"cookies": ["reese84"]},
    "imperva": {"cookies": ["incap_ses_"], "headers": ["x-cdn"]},
    "distil": {"cookies": ["distil"], "headers": ["x-distil"]},
}

# Multi-engine switch signals — triggers browser fallback / engine rotation
SWITCH_SIGNALS = {
    "captcha": r"(captcha|recaptcha|hcaptcha|turnstile)",
    "cloudflare_challenge": r"cf-challenge|_cf_chl_opt",
    "datadome_block": r"datadome|x-datadome",
    "empty_body": 200,  # less than 200 bytes = likely blocked
    "rate_limit": r"rate\.limit|too\.many\.requests|429",
    "waf_block": r"access\.denied|blocked|forbidden",
}


def _get_all_cookies(headers: dict) -> str:
    """Collect all Set-Cookie header values (handles multi-value headers)."""
    if hasattr(headers, "getall"):
        return "; ".join(headers.getall("set-cookie", []))
    parts = []
    for key, value in headers.items():
        if key.lower() == "set-cookie":
            parts.append(str(value))
    return "; ".join(parts)

class DecisionEngine:
    """Three-tier decision engine: L0 rules → L1 local model → L2 API."""
    
    def __init__(self, cache_backend=None, llm_client=None, l1_model=None):
        self._cache = cache_backend
        self._llm = llm_client
        self._l1_model = l1_model  # Ollama client or None
        self._decision_cache: dict[str, tuple[float, dict]] = {}
    
    async def analyze(self, html: str, headers: dict, status: int) -> dict[str, Any]:
        """Analyze page response for anti-crawl signals."""
        vendor = self._detect_vendor(html, headers)
        switch_signal = self._detect_switch_signals(html, headers, status)
        retry_after = int(headers.get("retry-after", 60))
        
        if switch_signal:
            return {
                "action": "switch_engine",
                "signal": switch_signal,
                "vendor": vendor,
                "retry_after": retry_after,
                "confidence": 0.9,
            }

        if status == 403 or status == 429:
            return {
                "action": "backoff",
                "vendor": vendor,
                "retry_after": retry_after,
                "confidence": 0.9,
            }
        
        if vendor:
            return {
                "action": "evade",
                "vendor": vendor,
                "recommended_engine": self._recommend_engine(vendor),
                "confidence": 0.8,
            }
        
        return {"action": "continue", "vendor": "none", "confidence": 0.5}
    
    async def recommend(self, url: str) -> dict[str, Any]:
        """Pre-flight: recommend strategy before first request."""
        cache_key = f"decision:{url}"
        
        # L0: in-memory cache
        if cache_key in self._decision_cache:
            ts, result = self._decision_cache[cache_key]
            if time.monotonic() - ts < 300:
                return result
        
        # L1: local model pre-flight
        if self._l1_model:
            try:
                l1_result = await self._run_l1(url)
                if l1_result:
                    self._decision_cache[cache_key] = (time.monotonic(), l1_result)
                    return l1_result
            except Exception as e:
                logger.warning(f"L1 model failed, falling back to L2: {e}")
        
        # L2: default / API-based
        result = {
            "entry_point": "http",
            "tls_profile": "chrome_124",
            "use_browser": False,
            "confidence": 0.7,
        }
        
        self._decision_cache[cache_key] = (time.monotonic(), result)
        return result
    
    async def _run_l1(self, url: str) -> dict | None:
        """L1: Use local Ollama model to predict difficulty and engine.
        
        Returns None if model is unavailable or confidence is too low.
        """
        domain = urlparse(url).netloc
        
        prompt = f"""Classify this website for web scraping difficulty. Domain: {domain}
        
Respond with JSON only:
{{"difficulty": "low|medium|high", "likely_engine": "httpx|playwright|camoufox|cloaked", "confidence": 0.0-1.0, "reason": "brief"}}"""
        
        try:
            response = await self._l1_model.generate(prompt, max_tokens=150)
            data = json.loads(response)
            if data.get("confidence", 0) < 0.5:
                return None
            
            engine_map = {
                "httpx": "http",
                "playwright": "playwright", 
                "camoufox": "camoufox",
                "cloaked": "cloaked",
            }
            
            return {
                "entry_point": engine_map.get(data.get("likely_engine", "httpx"), "http"),
                "tls_profile": "chrome_124",
                "use_browser": data.get("difficulty") in ("medium", "high"),
                "confidence": data.get("confidence", 0.5),
                "reason": data.get("reason", "L1 prediction"),
            }
        except Exception as e:
            logger.warning(f"L1 model failed: {e}")
            return None
    
    def _detect_vendor(self, html: str, headers: dict) -> str:
        """Detect anti-crawl vendor from headers/cookies/HTML."""
        # Collect all Set-Cookie values (supports multi-value headers)
        cookie_values = _get_all_cookies(headers)

        for vendor, sigs in VENDOR_SIGNATURES.items():
            for h in sigs.get("headers", []):
                if any(k.lower() == h.lower() for k in headers):
                    return vendor
            for c in sigs.get("cookies", []):
                if c.lower() in cookie_values.lower():
                    return vendor

        # F5 Shape: regex match for TS cookie patterns (e.g. TSa1b2c3d4)
        if re.search(r'TS[a-fA-F0-9]{6,}', cookie_values):
            return "f5_shape"

        return ""
    
    def _recommend_engine(self, vendor: str) -> str:
        engine_map = {
            "cloudflare": "camoufox",
            "akamai": "cloaked",
            "datadome": "cloaked",
            "perimeterx": "camoufox",
            "kasada": "cloaked",
            "f5_shape": "cloaked",
            "imperva": "playwright",
            "distil": "camoufox",
        }
        return engine_map.get(vendor, "cloaked")

    def _detect_switch_signals(self, html: str, headers: dict, status: int) -> str:
        """Detect engine-switch signals from HTML/headers/status."""
        html_lower = html.lower() if html else ""
        headers_str = str(headers).lower()

        # Check empty body (likely blocked)
        if status == 200 and len(html) < SWITCH_SIGNALS["empty_body"]:
            return "empty_body"

        # Check regex-based signals
        for signal_name, pattern in SWITCH_SIGNALS.items():
            if isinstance(pattern, int):
                continue  # skip non-regex entries
            if re.search(pattern, html_lower) or re.search(pattern, headers_str):
                return signal_name

        return ""
