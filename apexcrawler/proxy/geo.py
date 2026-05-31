"""GeoIP matching and country-level proxy routing.

Provides country-code resolution from IP addresses and domain names,
used by the proxy pool to route requests through geographically
appropriate proxies.

Supports:
- MaxMind GeoLite2 databases (local, fast)
- ip-api.com (free, no setup)
- ipinfo.io (paid, high accuracy)
- CacheLayer with TTL for repeated lookups
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class GeoInfo:
    """Geographic information for an IP address."""
    ip: str
    country_code: str = ""     # ISO 3166-1 alpha-2
    country_name: str = ""
    city: str = ""
    region: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    timezone: str = ""
    isp: str = ""
    org: str = ""
    asn: str = ""
    is_eu: bool = False
    cached: bool = False
    source: str = ""           # "maxmind", "ip-api", "ipinfo", "cache"


@dataclass
class ProxyRouteHint:
    """Suggests which geo/proxy to use for a target domain."""
    domain: str
    target_country: str = ""   # Where the target is hosted
    recommended_geo: str = ""  # Which proxy geo to use
    reason: str = ""
    confidence: float = 0.0


class GeoResolver:
    """Resolve IPs to geographic locations for proxy routing.

    Usage:
        resolver = GeoResolver(backend="ip-api")
        info = await resolver.lookup("8.8.8.8")
        print(f"{info.ip} → {info.country_code} ({info.country_name})")
    """

    def __init__(
        self,
        backend: str = "ip-api",
        cache_ttl: int = 3600,
        maxmind_db_path: str = "",
        ipinfo_token: str = "",
    ):
        """Initialize geo resolver.

        Args:
            backend: "maxmind", "ip-api", or "ipinfo".
            cache_ttl: How long to cache results (seconds).
            maxmind_db_path: Path to GeoLite2-Country.mmdb.
            ipinfo_token: API token for ipinfo.io.
        """
        self._backend = backend
        self._cache_ttl = cache_ttl
        self._maxmind_db_path = maxmind_db_path
        self._ipinfo_token = ipinfo_token

        # Local LRU cache
        self._cache: dict[str, tuple[float, GeoInfo]] = {}
        self._maxmind_reader: Any = None
        self._maxmind_available = False

        if backend == "maxmind" and maxmind_db_path:
            self._init_maxmind()

    def _init_maxmind(self) -> None:
        """Lazy-init the MaxMind reader."""
        try:
            import maxminddb
            self._maxmind_reader = maxminddb.open_database(self._maxmind_db_path)
            self._maxmind_available = True
            logger.info(f"MaxMind GeoIP database loaded: {self._maxmind_db_path}")
        except ImportError:
            logger.warning("maxminddb not installed; falling back to ip-api")
            self._backend = "ip-api"
        except FileNotFoundError:
            logger.warning(f"MaxMind DB not found: {self._maxmind_db_path}; falling back to ip-api")
            self._backend = "ip-api"
        except Exception as e:
            logger.warning(f"MaxMind init error: {e}; falling back to ip-api")
            self._backend = "ip-api"

    async def lookup(self, ip_or_host: str) -> GeoInfo:
        """Resolve geographic info for an IP or hostname.

        Args:
            ip_or_host: IP address or hostname to resolve.

        Returns:
            GeoInfo dataclass with location details.
        """
        # Resolve hostname → IP if needed
        ip = self._resolve_to_ip(ip_or_host)
        if not ip:
            return GeoInfo(ip=ip_or_host, country_code="")

        # Check cache
        cache_key = ip
        if cache_key in self._cache:
            ts, info = self._cache[cache_key]
            if time.monotonic() - ts < self._cache_ttl:
                info.cached = True
                return info

        # Lookup
        info = GeoInfo(ip=ip)
        if self._backend == "maxmind" and self._maxmind_available:
            info = self._lookup_maxmind(ip)
        elif self._backend == "ipinfo" and self._ipinfo_token:
            info = await self._lookup_ipinfo(ip)
        else:
            info = await self._lookup_ip_api(ip)

        # Cache
        if info.country_code:
            self._cache[cache_key] = (time.monotonic(), info)
            # Evict old entries if cache grows too large
            if len(self._cache) > 10000:
                self._evict_cache()

        return info

    async def lookup_batch(self, ips: list[str]) -> dict[str, GeoInfo]:
        """Look up multiple IPs concurrently.

        Args:
            ips: List of IP addresses.

        Returns:
            Dict of IP → GeoInfo.
        """
        import asyncio
        tasks = [self.lookup(ip) for ip in ips]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        result_map: dict[str, GeoInfo] = {}
        for ip, result in zip(ips, results):
            if isinstance(result, GeoInfo):
                result_map[ip] = result
            else:
                result_map[ip] = GeoInfo(ip=ip)
        return result_map

    # ── Backend Implementations ─────────────────────────────

    def _lookup_maxmind(self, ip: str) -> GeoInfo:
        """Look up using local MaxMind GeoLite2 database."""
        try:
            record = self._maxmind_reader.get(ip)
            if record:
                country = record.get("country", {})
                city = record.get("city", {})
                location = record.get("location", {})
                return GeoInfo(
                    ip=ip,
                    country_code=country.get("iso_code", ""),
                    country_name=country.get("names", {}).get("en", ""),
                    city=city.get("names", {}).get("en", ""),
                    region=record.get("subdivisions", [{}])[0].get("names", {}).get("en", ""),
                    latitude=location.get("latitude", 0.0) or 0.0,
                    longitude=location.get("longitude", 0.0) or 0.0,
                    timezone=location.get("time_zone", ""),
                    is_eu=country.get("is_in_european_union", False),
                    source="maxmind",
                )
        except Exception as e:
            logger.error(f"MaxMind lookup error for {ip}: {e}")
        return GeoInfo(ip=ip, source="maxmind")

    async def _lookup_ip_api(self, ip: str) -> GeoInfo:
        """Look up using the free ip-api.com service."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"http://ip-api.com/json/{ip}?fields=66846719")
                resp.raise_for_status()
                data = resp.json()
                if data.get("status") == "success":
                    return GeoInfo(
                        ip=ip,
                        country_code=data.get("countryCode", ""),
                        country_name=data.get("country", ""),
                        city=data.get("city", ""),
                        region=data.get("regionName", ""),
                        latitude=data.get("lat", 0.0) or 0.0,
                        longitude=data.get("lon", 0.0) or 0.0,
                        timezone=data.get("timezone", ""),
                        isp=data.get("isp", ""),
                        org=data.get("org", ""),
                        asn=data.get("as", ""),
                        source="ip-api",
                    )
        except Exception as e:
            logger.warning(f"ip-api.com lookup failed for {ip}: {e}")
        return GeoInfo(ip=ip, source="ip-api")

    async def _lookup_ipinfo(self, ip: str) -> GeoInfo:
        """Look up using ipinfo.io (requires token)."""
        try:
            headers = {"Authorization": f"Bearer {self._ipinfo_token}"}
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"https://ipinfo.io/{ip}/json", headers=headers)
                resp.raise_for_status()
                data = resp.json()
                loc = data.get("loc", "0,0").split(",")
                return GeoInfo(
                    ip=ip,
                    country_code=data.get("country", ""),
                    country_name=data.get("country", ""),
                    city=data.get("city", ""),
                    region=data.get("region", ""),
                    latitude=float(loc[0]) if len(loc) >= 2 else 0.0,
                    longitude=float(loc[1]) if len(loc) >= 2 else 0.0,
                    timezone=data.get("timezone", ""),
                    isp=data.get("org", ""),
                    org=data.get("org", ""),
                    asn=data.get("asn", {}).get("asn", "") if isinstance(data.get("asn"), dict) else "",
                    is_eu=data.get("is_eu", {}).get("country", False) if isinstance(data.get("is_eu"), dict) else False,
                    source="ipinfo",
                )
        except Exception as e:
            logger.warning(f"ipinfo.io lookup failed for {ip}: {e}")
        return GeoInfo(ip=ip, source="ipinfo")

    # ── DNS Resolution ──────────────────────────────────────

    @staticmethod
    def _resolve_to_ip(host: str) -> str:
        """Resolve a hostname to an IP address. Returns the original string if it is already an IP."""
        host = host.strip()
        # Already an IP
        try:
            ipaddress.ip_address(host)
            return host
        except ValueError:
            pass

        # Resolve hostname
        try:
            return socket.gethostbyname(host)
        except socket.gaierror:
            logger.debug(f"Cannot resolve hostname: {host}")
            return ""

    # ── Cache Management ────────────────────────────────────

    def _evict_cache(self) -> None:
        """Evict expired entries from the cache."""
        now = time.monotonic()
        expired = [k for k, (ts, _) in self._cache.items() if now - ts > self._cache_ttl]
        for k in expired:
            del self._cache[k]
        logger.debug(f"Geo cache evicted {len(expired)} expired entries")

    def cache_size(self) -> int:
        """Return current cache size."""
        return len(self._cache)

    def clear_cache(self) -> None:
        """Clear all cached geo lookups."""
        self._cache.clear()

    # ── Proxy Routing ───────────────────────────────────────

    async def suggest_proxy_geo(self, target_url: str) -> ProxyRouteHint:
        """Suggest which proxy geo to use for a given target URL.

        If the target is in the EU, prefer EU proxies. If in China,
        prefer Asian proxies for lower latency.

        Args:
            target_url: The URL you want to scrape.

        Returns:
            ProxyRouteHint with recommended geo and reasoning.
        """
        from urllib.parse import urlparse

        parsed = urlparse(target_url)
        domain = parsed.netloc or parsed.hostname or target_url
        if not domain:
            return ProxyRouteHint(domain=target_url, reason="unable to parse domain")

        info = await self.lookup(domain)
        if not info.country_code:
            return ProxyRouteHint(domain=domain, reason="unable to resolve geo")

        hint = ProxyRouteHint(domain=domain, target_country=info.country_code)

        # EU region matching
        if info.is_eu or info.country_code in _EU_COUNTRIES:
            hint.recommended_geo = info.country_code
            hint.reason = f"Target in EU ({info.country_code}), prefer EU proxy for compliance"
            hint.confidence = 0.8

        # Same-country preference
        elif info.country_code in _HIGH_LATENCY_REGIONS:
            hint.recommended_geo = info.country_code
            hint.reason = f"Target in {info.country_code}, prefer local proxy for latency"
            hint.confidence = 0.7

        # Default: match target continent
        else:
            hint.recommended_geo = info.country_code
            hint.reason = f"Suggest proxy in {info.country_code} to match target"
            hint.confidence = 0.6

        return hint


# ── Geographic region constants ─────────────────────────────

_EU_COUNTRIES: frozenset[str] = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
})

_HIGH_LATENCY_REGIONS: frozenset[str] = frozenset({
    "CN", "IN", "BR", "RU", "ID", "ZA", "AU", "NZ", "JP", "KR",
    "SG", "HK", "TW", "TH", "VN", "MY", "PH",
})

# Country code → Continent mapping
_COUNTRY_TO_CONTINENT: dict[str, str] = {}
for _cc in _EU_COUNTRIES:
    _COUNTRY_TO_CONTINENT[_cc] = "EU"
for _cc in {"US", "CA", "MX"}:
    _COUNTRY_TO_CONTINENT[_cc] = "NA"
for _cc in {"CN", "JP", "KR", "IN", "SG", "HK", "TW", "TH", "VN", "MY", "PH", "ID", "PK", "BD"}:
    _COUNTRY_TO_CONTINENT[_cc] = "AS"
for _cc in {"BR", "AR", "CL", "CO", "PE", "VE"}:
    _COUNTRY_TO_CONTINENT[_cc] = "SA"
for _cc in {"AU", "NZ"}:
    _COUNTRY_TO_CONTINENT[_cc] = "OC"
for _cc in {"ZA", "NG", "KE", "EG", "MA"}:
    _COUNTRY_TO_CONTINENT[_cc] = "AF"


def get_continent(country_code: str) -> str:
    """Get continent code from country code."""
    return _COUNTRY_TO_CONTINENT.get(country_code.upper(), "")


def is_eu_country(country_code: str) -> bool:
    """Check if a country code is in the EU."""
    return country_code.upper() in _EU_COUNTRIES
