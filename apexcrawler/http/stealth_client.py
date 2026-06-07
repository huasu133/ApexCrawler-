"""Stealth HTTP client using curl_cffi for TLS fingerprint impersonation.

Supports Chrome 131 impersonation with proper headers and proxy.
"""

from __future__ import annotations

from typing import Optional

from curl_cffi import requests as curl_requests


class StealthHTTPClient:
    """HTTP client that mimics real browser TLS fingerprints."""

    def __init__(self, impersonate: str = "chrome131", proxy: Optional[str] = None):
        self.impersonate = impersonate
        self.proxy = proxy
        self.session = curl_requests.Session()

    def get(self, url: str, **kwargs):
        """GET request with browser impersonation."""
        params = {
            "impersonate": self.impersonate,
        }
        if self.proxy:
            params["proxies"] = {"http": self.proxy, "https": self.proxy}
        params.update(kwargs)
        return self.session.get(url, **params)

    def set_cookies(self, cookies: dict):
        """Set cookies for the session."""
        for name, value in cookies.items():
            self.session.cookies.set(name, value)

    def close(self):
        self.session.close()
