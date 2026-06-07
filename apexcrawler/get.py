"""
ApexCrawler one-liner API.

Usage:
    from apexcrawler import get
    html = get("https://example.com")
    html = get("https://example.com", engine="cloaked_v2")
"""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)


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
                import re
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
        return ""
