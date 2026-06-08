"""
ApexCrawler one-liner API.

Usage:
    from apexcrawler import get
    html = get("https://example.com")
    html = get("https://example.com", engine="cloaked_v2")
"""
from __future__ import annotations
import ipaddress
import logging
import re
import socket
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# SSRF Protection
# ══════════════════════════════════════════════════════════════════════

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_url(url: str) -> None:
    """Validate URL to prevent SSRF attacks. Raises ValueError if blocked."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Blocked scheme: {parsed.scheme}")
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError(f"Blocked host: {host}")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP — resolve via DNS
        try:
            resolved = socket.gethostbyname(host)
            addr = ipaddress.ip_address(resolved)
        except socket.gaierror:
            return  # Cannot resolve — let the caller handle
    else:
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise ValueError(f"Blocked IP: {host} (in {net})")


def get(url: str, engine: str = "", proxy: str = "", timeout: int = 30,
        output: str = "html") -> str:
    """Fetch a URL and return its content as a string.

    Simple one-liner, like requests.get().
    Returns HTML by default. Use output="text" for plain text.

    Note: The `engine` parameter is reserved for future browser-engine
    support; currently always uses HTTP FastFetcher.

    Examples:
        html = get("https://example.com")
        text = get("https://example.com", output="text")
        html = get("https://example.com", engine="cloaked_v2")
    """
    if engine:
        logger.warning("engine parameter is not yet supported (using default HTTP fetcher)")

    # SSRF validation
    _validate_url(url)

    try:
        from apexcrawler.http.fetcher import FastFetcher
        fetcher = FastFetcher(impersonate="chrome131", proxy=proxy if proxy else None, timeout=timeout)
        try:
            result = fetcher.get(url)
            status = result.get("status_code", 0)
            text = result.get("html", "") or result.get("text", "")

            if status != 200:
                logger.warning("HTTP %s for %s", status, url)

            if output == "text" and text:
                # Remove script and style content first (may contain non-text)
                text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
                # Strip remaining HTML tags
                text = re.sub(r'<[^>]+>', '', text)
                # Normalize whitespace
                text = re.sub(r'\s+', ' ', text).strip()

            return text
        finally:
            fetcher.close()
    except ImportError as e:
        module = e.name or "unknown"
        raise ImportError(
            f"Missing dependency: {module}\n  Fix: pip install {module}"
        ) from e
    except Exception as e:
        logger.error("Failed to fetch %s: %s", url, e)
        logger.debug("Full traceback:", exc_info=True)
        return ""
