"""Fast HTTP fetcher for simple pages — no browser overhead.

Uses curl_cffi for TLS fingerprint impersonation.
Designed as a lightweight alternative to Playwright-based engines
for pages that don't require JavaScript rendering.
"""

from __future__ import annotations

import logging
from typing import Optional

from curl_cffi import requests

logger = logging.getLogger(__name__)


class FastFetcher:
    """Fast HTTP fetcher for simple pages — no browser overhead.

    Supports Chrome/Firefox/Safari TLS fingerprint impersonation via
    curl_cffi.  Ideal for static pages, APIs, and pre-rendered content.

    Usage::

        fetcher = FastFetcher(impersonate="chrome131")
        result = fetcher.get("https://example.com")
        logger.debug("status=%s text=%s...", result["status_code"], result["text"][:200])
        fetcher.close()
    """

    def __init__(
        self,
        impersonate: str = "chrome131",
        proxy: Optional[str] = None,
        timeout: int = 30,
    ):
        """Initialise the fast fetcher.

        Args:
            impersonate: TLS fingerprint profile
                         (e.g. ``"chrome131"``, ``"firefox133"``, ``"safari17"``).
            proxy:       Optional proxy URL (``http://user:pass@host:port``).
            timeout:     Request timeout in seconds.
        """
        self.impersonate = impersonate
        self.proxy = proxy
        self.timeout = timeout
        self.session = requests.Session()

    def get(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, str | int]:
        """GET request returning a normalised response dict.

        Args:
            url:     Target URL.
            headers: Optional extra request headers.

        Returns:
            Dict with keys ``status_code``, ``headers``, ``text``,
            ``html``, ``url``.
        """
        params: dict = {
            "impersonate": self.impersonate,
            "timeout": self.timeout,
        }
        if self.proxy:
            params["proxies"] = {"http": self.proxy, "https": self.proxy}
        if headers:
            params["headers"] = headers

        resp = self.session.get(url, **params)
        return {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "text": resp.text,
            "html": resp.text,
            "url": str(resp.url),
        }

    def post(
        self,
        url: str,
        data: Optional[dict[str, str]] = None,
        json_data: Optional[dict[str, str]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, str | int]:
        """POST request returning a normalised response dict.

        Args:
            url:      Target URL.
            data:     Form-encoded body.
            json_data: JSON-serialisable body.
            headers:  Optional extra request headers.

        Returns:
            Dict with keys ``status_code``, ``headers``, ``text``,
            ``html``, ``url``.
        """
        params: dict = {
            "impersonate": self.impersonate,
            "timeout": self.timeout,
        }
        if self.proxy:
            params["proxies"] = {"http": self.proxy, "https": self.proxy}
        if headers:
            params["headers"] = headers
        if json_data is not None:
            params["json"] = json_data
        elif data is not None:
            params["data"] = data

        resp = self.session.post(url, **params)
        return {
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "text": resp.text,
            "html": resp.text,
            "url": str(resp.url),
        }

    def close(self) -> None:
        """Close the underlying curl_cffi session."""
        try:
            self.session.close()
            logger.debug("FastFetcher session closed")
        except Exception:
            pass

    def __enter__(self) -> FastFetcher:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
