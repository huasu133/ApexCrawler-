"""
ApexCrawler one-liner API.

Usage:
    from apexcrawler import get
    html = get("https://example.com")
    html = get("https://example.com", engine="cloaked_v2")
    html = get("https://example.com", engine="vanilla", proxy="http://user:pass@host:port")
"""
from __future__ import annotations
import asyncio
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


def _extract_text(html: str) -> str:
    """Strip HTML tags and normalize whitespace for text output."""
    text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


async def _fetch_via_engine(
    engine_name: str, url: str, proxy: str, timeout: int
) -> str:
    """Fetch a URL using a browser engine. Internal helper for get()."""
    from apexcrawler.routing.registry import EngineRegistry

    engine_cls = EngineRegistry.get(engine_name)
    if engine_cls is None:
        logger.warning("Unknown engine '%s', falling back to FastFetcher", engine_name)
        return ""

    engine = engine_cls()
    try:
        await engine.launch()
        page = await engine.navigate(url, proxy=proxy if proxy else None)
        html = await page.content()
        await page.close()
        return html
    finally:
        await engine.close()


def get(url: str, engine: str = "", proxy: str = "", timeout: int = 30,
        output: str = "html") -> str:
    """Fetch a URL and return its content as a string.

    Simple one-liner, like requests.get().
    Returns HTML by default. Use output="text" for plain text.

    When engine is specified (e.g. "vanilla", "patched", "camoufox",
    "cloaked", "cloaked_v2", "pydoll"), uses the corresponding browser
    engine.  Falls back to HTTP FastFetcher on engine failure.

    Examples:
        html = get("https://example.com")
        text = get("https://example.com", output="text")
        html = get("https://example.com", engine="cloaked_v2")
    """

    # SSRF validation (always applies, regardless of engine)
    _validate_url(url)

    # ── Browser engine path ────────────────────────────────────
    if engine:
        try:
            html = asyncio.run(
                _fetch_via_engine(engine, url, proxy, timeout)
            )
            if html:
                logger.info("Engine '%s' fetched %s (%d bytes)", engine, url, len(html))
                return _extract_text(html) if output == "text" else html
            # Empty result from engine — fall through to FastFetcher
            logger.warning("Engine '%s' returned empty content, falling back", engine)
        except ImportError:
            raise
        except Exception as e:
            logger.warning(
                "Engine '%s' failed for %s: %s, falling back to FastFetcher",
                engine, url, e,
            )
            logger.debug("Full traceback:", exc_info=True)

    # ── HTTP FastFetcher path (default / fallback) ─────────────
    try:
        from apexcrawler.http.fetcher import FastFetcher
        fetcher = FastFetcher(
            impersonate="chrome131",
            proxy=proxy if proxy else None,
            timeout=timeout,
        )
        try:
            result = fetcher.get(url)
            status = result.get("status_code", 0)
            text = result.get("html", "") or result.get("text", "")

            if status != 200:
                logger.warning("HTTP %s for %s", status, url)

            if output == "text" and text:
                text = _extract_text(text)

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
