"""CAPTCHA solver integration — 2captcha / Capsolver / Anti-Captcha.

Provides a unified interface for solving reCAPTCHA v2/v3, hCaptcha, and
image-based CAPTCHAs via third-party solving services.

Status: INTEGRATED — API stubs ready; requires valid API key to solve.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# ── Price reference (2026) ──────────────────────────────────

CAPTCHA_PRICES: dict[str, dict[str, str]] = {
    "2captcha": {
        "recaptcha_v2": "$0.50-1.00/1000",
        "recaptcha_v3": "$1.00-2.00/1000",
    },
    "capsolver": {
        "recaptcha_v2": "$0.80/1000",
        "recaptcha_v3": "$1.50/1000",
    },
    "anticaptcha": {
        "recaptcha_v2": "$0.50/1000",
        "recaptcha_v3": "$1.00/1000",
    },
}

# ── Service endpoints ───────────────────────────────────────

_SERVICE_ENDPOINTS = {
    "2captcha": {
        "in": "https://2captcha.com/in.php",
        "res": "https://2captcha.com/res.php",
    },
    "capsolver": {
        "in": "https://api.capsolver.com/createTask",
        "res": "https://api.capsolver.com/getTaskResult",
    },
    "anticaptcha": {
        "in": "https://api.anti-captcha.com/createTask",
        "res": "https://api.anti-captcha.com/getTaskResult",
    },
}


class CaptchaSolver:
    """Unified CAPTCHA solving client.

    Supports 2captcha, Capsolver, and Anti-Captcha backends for solving
    reCAPTCHA v2/v3, hCaptcha, and image-based CAPTCHAs.

    Usage:
        solver = CaptchaSolver(api_key="YOUR_KEY", service="2captcha")
        token = await solver.solve_recaptcha(site_key="...", page_url="...")
    """

    def __init__(self, api_key: str = "", service: str = "2captcha") -> None:
        if service not in _SERVICE_ENDPOINTS:
            raise ValueError(
                f"Unknown service '{service}'. "
                f"Available: {list(_SERVICE_ENDPOINTS.keys())}"
            )
        self._api_key = api_key
        self._service = service

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def service(self) -> str:
        return self._service

    # ── reCAPTCHA ──────────────────────────────────────────

    async def solve_recaptcha(
        self, site_key: str, page_url: str, version: str = "v2"
    ) -> str | None:
        """Solve reCAPTCHA v2/v3 via external API.

        Args:
            site_key: The reCAPTCHA site key from the target page.
            page_url: The full URL of the page containing the CAPTCHA.
            version: "v2" (default) or "v3".

        Returns:
            g-recaptcha-response token, or None if solving failed.
        """
        if not self._api_key:
            logger.warning("No CAPTCHA API key configured — cannot solve reCAPTCHA")
            return None

        logger.info(
            "Requesting reCAPTCHA %s solution from %s (site_key=%s)",
            version, self._service, site_key[:16]
        )

        if self._service == "2captcha":
            return await self._solve_2captcha_recaptcha(site_key, page_url, version)
        elif self._service in ("capsolver", "anticaptcha"):
            return await self._solve_task_based_recaptcha(
                site_key, page_url, version
            )
        return None

    # ── hCaptcha ───────────────────────────────────────────

    async def solve_hcaptcha(
        self, site_key: str, page_url: str
    ) -> str | None:
        """Solve hCaptcha via external API.

        Args:
            site_key: The hCaptcha site key from the target page.
            page_url: The full URL of the page containing the CAPTCHA.

        Returns:
            h-captcha-response token, or None if solving failed.
        """
        if not self._api_key:
            logger.warning("No CAPTCHA API key configured — cannot solve hCaptcha")
            return None

        logger.info(
            "Requesting hCaptcha solution from %s", self._service
        )

        if self._service == "2captcha":
            return await self._solve_2captcha_hcaptcha(site_key, page_url)
        return None

    # ── Image CAPTCHA ──────────────────────────────────────

    async def solve_image(self, image_base64: str) -> str | None:
        """Solve an image-based CAPTCHA.

        Args:
            image_base64: Base64-encoded CAPTCHA image.

        Returns:
            Decoded text from the CAPTCHA image, or None if solving failed.
        """
        if not self._api_key:
            logger.warning("No CAPTCHA API key configured — cannot solve image CAPTCHA")
            return None

        logger.info("Requesting image CAPTCHA solution from %s", self._service)
        raise NotImplementedError("Image CAPTCHA solving not yet implemented")

    # ── 2captcha implementation ────────────────────────────

    async def _solve_2captcha_recaptcha(
        self, site_key: str, page_url: str, version: str
    ) -> str | None:
        """Solve reCAPTCHA via 2captcha API.

        Flow:
        1. POST https://2captcha.com/in.php
           ?key=API_KEY&method=userrecaptcha&googlekey=SITE_KEY&pageurl=URL
        2. Poll GET https://2captcha.com/res.php
           ?key=API_KEY&action=get&id=CAPTCHA_ID
        """
        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp required for 2captcha integration")
            return None

        method = "userrecaptcha" if version == "v2" else "userrecaptchav3"
        params = {
            "key": self._api_key,
            "method": method,
            "googlekey": site_key,
            "pageurl": page_url,
            "json": 1,
        }

        async with aiohttp.ClientSession() as session:
            # Step 1: Submit CAPTCHA
            async with session.post(
                _SERVICE_ENDPOINTS["2captcha"]["in"], data=params
            ) as resp:
                data = await resp.json()
                if data.get("status") != 1:
                    logger.error("2captcha submission failed: %s", data.get("request"))
                    return None
                captcha_id = data["request"]

            # Step 2: Poll for result
            res_params = {
                "key": self._api_key,
                "action": "get",
                "id": captcha_id,
                "json": 1,
            }
            for attempt in range(30):  # ~2.5 minutes max
                await asyncio.sleep(5)
                async with session.get(
                    _SERVICE_ENDPOINTS["2captcha"]["res"], params=res_params
                ) as resp:
                    data = await resp.json()
                    if data.get("status") == 1:
                        logger.info("2captcha solved in %.0fs", (attempt + 1) * 5)
                        return data["request"]
                    if data.get("request") != "CAPCHA_NOT_READY":
                        logger.error("2captcha error: %s", data.get("request"))
                        return None

        logger.warning("2captcha timed out after 30 attempts")
        return None

    async def _solve_2captcha_hcaptcha(
        self, site_key: str, page_url: str
    ) -> str | None:
        """Solve hCaptcha via 2captcha API."""
        logger.warning("hCaptcha solving via 2captcha not yet implemented")
        raise NotImplementedError("hCaptcha solving via 2captcha not yet implemented")

    # ── Task-based API implementation (Capsolver, Anti-Captcha) ──

    async def _solve_task_based_recaptcha(
        self, site_key: str, page_url: str, version: str
    ) -> str | None:
        """Solve reCAPTCHA via task-based API (Capsolver / Anti-Captcha).

        Flow:
        1. POST createTask with RecaptchaV2Task / RecaptchaV3TaskProxyless
        2. Poll getTaskResult until solved
        """
        logger.warning(
            "Task-based reCAPTCHA solving via %s not yet implemented", self._service
        )
        raise NotImplementedError(
            f"Task-based reCAPTCHA solving via {self._service} not yet implemented"
        )
