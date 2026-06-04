"""Mobile API endpoint sniffer — auto-detect faster API endpoints.

Many sites expose mobile/JSON APIs that return clean structured data
with weaker anti-bot protection than desktop HTML pages.

Priority: mobile subdomain → API subdomain → JSON endpoint → fallback
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse, urlunparse
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

MOBILE_SUBDOMAINS = ["m.", "mobile.", "touch.", "wap.", "h5."]
API_SUBDOMAINS = ["api.", "api-v2.", "api-v1.", "json."]
JSON_PATH_PATTERNS = ["/api/", "/graphql", "/.json", "/data/"]


@dataclass
class MobileEndpoint:
    """A discovered faster endpoint alternative."""
    url: str
    source: str  # "mobile_subdomain", "api_subdomain", "json_path"
    confidence: float = 0.5
    size_bytes: int = 0
    content_type: str = ""


class MobileAPISniffer:
    """Auto-detect and prioritize mobile/API endpoints for faster extraction."""

    def __init__(self, http_client=None):
        self._client = http_client
        self._cache: dict[str, MobileEndpoint | None] = {}

    def generate_candidates(self, url: str) -> list[str]:
        """Generate alternative endpoint candidates from a desktop URL."""
        parsed = urlparse(url)
        domain = parsed.netloc
        base = domain.split("www.")[-1] if domain.startswith("www.") else domain
        path = parsed.path
        candidates = []

        # Mobile subdomain: www.amazon.com → m.amazon.com
        for prefix in MOBILE_SUBDOMAINS:
            candidates.append(f"{parsed.scheme}://{prefix}{base}{path}")

        # API subdomain: www.shop.com → api.shop.com
        for prefix in API_SUBDOMAINS:
            candidates.append(f"{parsed.scheme}://{prefix}{base}{path}")
            candidates.append(f"{parsed.scheme}://{prefix}{base}/products{path}")

        # JSON path: /product/123 → /api/product/123 or /product/123.json
        for pattern in JSON_PATH_PATTERNS:
            if not path.endswith(tuple(JSON_PATH_PATTERNS)):
                candidates.append(f"{parsed.scheme}://{domain}{path}.json")
                candidates.append(f"{parsed.scheme}://{domain}{pattern.rstrip('/')}{path}")

        return candidates

    async def probe(self, url: str) -> MobileEndpoint | None:
        """Probe candidates and return the best one."""
        if url in self._cache:
            return self._cache[url]

        candidates = self.generate_candidates(url)[:6]  # Limit to avoid flood

        try:
            import httpx
        except ImportError as e:
            logger.warning(f"httpx import failed: {e}")
            return None

        from apexcrawler.utils.dns_cache import dns_cache

        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            for candidate in candidates:
                try:
                    # DNS cache: resolve host to IP for faster connection
                    parsed = urlparse(candidate)
                    host = parsed.netloc.split(":")[0]
                    resolved_ip = dns_cache.resolve(host)
                    if resolved_ip != host:
                        netloc = parsed.netloc.replace(host, resolved_ip)
                        candidate = urlunparse(parsed._replace(netloc=netloc))
                        headers = {"Host": host}
                        resp = await client.head(candidate, headers=headers)
                    else:
                        resp = await client.head(candidate)
                    if resp.status_code < 400:
                        ct = resp.headers.get("content-type", "")
                        is_json = "json" in ct.lower()
                        size = int(resp.headers.get("content-length", 0))
                        confidence = 0.9 if is_json else 0.6
                        endpoint = MobileEndpoint(
                            url=candidate,
                            source="mobile" if any(p in candidate for p in MOBILE_SUBDOMAINS) else "api",
                            confidence=confidence,
                            size_bytes=size,
                            content_type=ct,
                        )
                        self._cache[url] = endpoint
                        logger.info(f"Mobile API found: {candidate} (confidence={confidence:.1f})")
                        return endpoint
                except Exception:
                    continue

        self._cache[url] = None
        return None

    def try_mobile_url(self, url: str) -> str | None:
        """Fast lookup from cache. Returns mobile URL if previously discovered."""
        cached = self._cache.get(url)
        if cached:
            return cached.url
        return None
