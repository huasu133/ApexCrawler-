"""LLM-based adaptive decision engine with three-tier caching."""

from __future__ import annotations
import logging, time
from typing import Any

logger = logging.getLogger(__name__)

# Anti-crawl vendor signatures
VENDOR_SIGNATURES = {
    "cloudflare": {"headers": ["cf-ray", "cf-chl-out", "cf-chl-bypass"], "cookies": ["cf_clearance", "__cf_bm"]},
    "akamai": {"cookies": ["_abck", "ak_bmsc", "bm_sz"]},
    "datadome": {"cookies": ["datadome"], "headers": ["x-datadome"]},
    "perimeterx": {"cookies": ["_px3", "_pxde", "_pxhd"]},
    "kasada": {"headers": ["x-kpsdk-ct", "x-kpsdk-cd"]},
    "f5_shape": {"cookies": ["reese84", "TS01", "TSxxxxxxxx"]},
}

class DecisionEngine:
    """Three-tier decision engine: L0 rules → L1 local model → L2 API."""
    
    def __init__(self, cache_backend=None, llm_client=None):
        self._cache = cache_backend
        self._llm = llm_client
        self._decision_cache: dict[str, tuple[float, dict]] = {}
    
    async def analyze(self, html: str, headers: dict, status: int) -> dict[str, Any]:
        """Analyze page response for anti-crawl signals."""
        vendor = self._detect_vendor(html, headers)
        retry_after = int(headers.get("retry-after", 60))
        
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
        
        # Default: start with HTTP layer
        result = {
            "entry_point": "http",
            "tls_profile": "chrome_124",
            "use_browser": False,
            "confidence": 0.7,
        }
        
        self._decision_cache[cache_key] = (time.monotonic(), result)
        return result
    
    def _detect_vendor(self, html: str, headers: dict) -> str:
        """Detect anti-crawl vendor from headers/cookies/HTML."""
        for vendor, sigs in VENDOR_SIGNATURES.items():
            for h in sigs.get("headers", []):
                if any(k.lower() == h.lower() for k in headers):
                    return vendor
            for c in sigs.get("cookies", []):
                if c.lower() in str(headers.get("set-cookie", "")).lower():
                    return vendor
        return ""
    
    def _recommend_engine(self, vendor: str) -> str:
        engine_map = {
            "cloudflare": "camoufox",
            "akamai": "cloaked",
            "datadome": "cloaked",
            "perimeterx": "camoufox",
            "kasada": "patched",
            "f5_shape": "cloaked",
        }
        return engine_map.get(vendor, "cloaked")
