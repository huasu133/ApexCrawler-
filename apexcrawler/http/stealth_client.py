"""Stealth HTTP client using curl_cffi for TLS fingerprint impersonation.

Supports Chrome 131 impersonation with proper headers, proxy, cookie injection,
auto-retry, and response normalization.  Two-way compatibility:
- StealthClient:  full-featured class (retry, Playwright cookie injection, context manager)
- StealthHTTPClient:  alias kept for backwards compatibility.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, Optional

from curl_cffi import requests as curl_requests

logger = logging.getLogger(__name__)

# ── Default headers (Chrome 131 on Windows) ──────────────────────────

DEFAULT_HEADERS: dict[str, str] = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": (
        '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"'
    ),
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


class StealthHTTPClient:
    """HTTP client that mimics real browser TLS fingerprints.

    Enhanced version with retry, cookie management, and response
    normalisation.  Supports context-manager protocol.
    """

    def __init__(
        self,
        impersonate: str = "chrome131",
        proxy: Optional[str] = None,
        cookies: Optional[dict[str, str]] = None,
        retry_times: int = 3,
        retry_delay: float = 2.0,
        timeout: int = 30,
    ):
        self.impersonate = impersonate
        self.proxy = proxy
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self.timeout = timeout
        self._headers = dict(DEFAULT_HEADERS)
        self.session = curl_requests.Session(impersonate=impersonate)

        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

        if cookies:
            for name, value in cookies.items():
                self.session.cookies.set(name, value)

    # ── Cookie management ───────────────────────────────────────────

    def set_cookies(self, cookies: dict[str, str]) -> None:
        """Set cookies from a plain dict {name: value}."""
        for name, value in cookies.items():
            self.session.cookies.set(name, value)

    def set_cookies_from_playwright(
        self, pw_cookies: list[dict[str, Any]]
    ) -> None:
        """Inject Playwright-exported cookies into the curl_cffi session.

        Playwright ``context.cookies()`` returns::

            [{"name": "xxx", "value": "yyy", "domain": ".qidian.com", …}, …]
        """
        for c in pw_cookies:
            name = c.get("name", "")
            value = c.get("value", "")
            if name and value:
                self.session.cookies.set(name, value)
                logger.debug("Cookie injected: %s", name)

        logger.info(
            "Injected %d cookies from Playwright", len(pw_cookies)
        )

    def get_cookie_dict(self) -> dict[str, str]:
        """Return all session cookies as a plain dict."""
        return {
            c.name: c.value for c in self.session.cookies
        }

    # ── Header management ───────────────────────────────────────────

    def set_header(self, name: str, value: str) -> None:
        """Set a single default header."""
        self._headers[name] = value

    def set_headers(self, headers: dict[str, str]) -> None:
        """Batch-set default headers."""
        self._headers.update(headers)

    def update_user_agent(self, ua: str) -> None:
        """Update the User-Agent header."""
        self._headers["User-Agent"] = ua
        logger.debug("User-Agent updated")

    # ── HTTP methods ────────────────────────────────────────────────

    def get(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """GET request with auto-retry.

        Returns a normalised response dict.
        """
        merged_headers = {**self._headers, **(headers or {})}
        return self._request_with_retry(
            method=self.session.get,
            url=url,
            params=params,
            headers=merged_headers,
            **kwargs,
        )

    def post(
        self,
        url: str,
        data: Optional[Any] = None,
        json_data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
        **kwargs,
    ) -> dict[str, Any]:
        """POST request with auto-retry.

        Returns a normalised response dict.
        """
        merged_headers = {**self._headers, **(headers or {})}
        if json_data is not None:
            merged_headers.setdefault("Content-Type", "application/json")
            return self._request_with_retry(
                method=self.session.post,
                url=url,
                json=json_data,
                headers=merged_headers,
                **kwargs,
            )
        return self._request_with_retry(
            method=self.session.post,
            url=url,
            data=data,
            headers=merged_headers,
            **kwargs,
        )

    # ── Retry & normalisation ───────────────────────────────────────

    def _request_with_retry(
        self,
        method: Callable[..., Any],
        url: str,
        **kwargs,
    ) -> dict[str, Any]:
        """Execute a request with automatic retry on errors.

        Retry conditions:
        - Network errors (timeout, DNS, connection reset)
        - Server errors (5xx)
        - 429 Too Many Requests

        Does NOT retry on 4xx client errors (except 429).
        """
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retry_times + 1):
            try:
                resp = method(url, timeout=self.timeout, **kwargs)
                status = getattr(resp, "status_code", 0)

                # Server error → retry
                if status >= 500:
                    logger.warning(
                        "Server error %d on %s (attempt %d/%d)",
                        status, url, attempt, self.retry_times,
                    )
                    if attempt < self.retry_times:
                        time.sleep(self.retry_delay * attempt)
                        continue

                # Rate-limit → retry with Retry-After
                if status == 429:
                    retry_after = _extract_retry_after(resp)
                    logger.warning(
                        "Rate-limited 429 on %s (attempt %d/%d), "
                        "waiting %.1fs",
                        url, attempt, self.retry_times, retry_after,
                    )
                    if attempt < self.retry_times:
                        time.sleep(retry_after)
                        continue

                return self._normalize_response(resp)

            except Exception as e:
                last_error = e
                logger.warning(
                    "Request failed on %s (attempt %d/%d): %s: %s",
                    url, attempt, self.retry_times,
                    type(e).__name__, e,
                )
                if attempt < self.retry_times:
                    time.sleep(self.retry_delay * attempt)

        error_msg = (
            f"Request failed after {self.retry_times} retries: {url}"
        )
        if last_error:
            error_msg += f" ({type(last_error).__name__}: {last_error})"

        logger.error(error_msg)
        return {
            "status_code": 0,
            "headers": {},
            "text": "",
            "json": None,
            "cookies": {},
            "error": str(last_error) if last_error else "max retries exceeded",
        }

    @staticmethod
    def _normalize_response(resp: Any) -> dict[str, Any]:
        """Normalise a curl_cffi Response into a plain dict."""
        try:
            body_json = resp.json()
        except (json.JSONDecodeError, ValueError, AttributeError):
            body_json = None

        cookies_dict = {}
        try:
            for cookie in resp.cookies:
                cookies_dict[cookie.name] = cookie.value
        except Exception:
            pass

        return {
            "status_code": getattr(resp, "status_code", 0),
            "headers": dict(getattr(resp, "headers", {})),
            "text": getattr(resp, "text", ""),
            "json": body_json,
            "cookies": cookies_dict,
        }

    # ── Resource management ─────────────────────────────────────────

    def close(self) -> None:
        """Close the underlying curl_cffi session."""
        try:
            self.session.close()
            logger.debug("StealthHTTPClient session closed")
        except Exception:
            pass

    def __enter__(self) -> StealthHTTPClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


# ── Backward-compatible alias ────────────────────────────────────────

StealthClient = StealthHTTPClient


# ── Helpers ───────────────────────────────────────────────────────────

def _extract_retry_after(resp: Any) -> float:
    """Extract ``Retry-After`` header value, default to 5 s."""
    try:
        headers = getattr(resp, "headers", {}) or {}
        val = headers.get("Retry-After", headers.get("retry-after", ""))
        if val:
            return float(val)
    except (ValueError, TypeError):
        pass
    return 5.0
