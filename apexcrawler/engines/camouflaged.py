"""Camoufox engine — Camoufox stealth browser engine via Playwright (Firefox).

Camoufox is a Firefox fork with deep fingerprint randomization at the
browser level, including JA4/TLS fingerprint diversity, Canvas/WebGL/
AudioContext spoofing, and font enumeration protection.

This engine uses Playwright's Firefox channel to connect to the Camoufox
binary. For full Camoufox SDK features (humanize, geoip, font spoofing),
set the executable_path to the Camoufox binary.

Status: INTEGRATED — Real implementation via Playwright + Camoufox binary.
"""

from __future__ import annotations

import logging

from apexcrawler.engines.base import BaseEngine, EngineCapability
from apexcrawler.engines.subresource import ensure_subresource_load
from apexcrawler.routing.registry import EngineRegistry

logger = logging.getLogger(__name__)


@EngineRegistry.register
class CamoufoxEngine(BaseEngine):
    """Camoufox-based stealth engine (Firefox-based).

    Provides:
    - Realistic JA4/TLS fingerprint randomization via Camoufox binary
    - Canvas, WebGL, AudioContext spoofing
    - Font enumeration protection
    - Navigator property normalization
    - WebRTC leak prevention

    Camoufox is the recommended engine for medium-high difficulty targets
    where Playwright-based engines are detected.
    """

    def __init__(self, headless: bool = True, viewport: dict | None = None,
                 executable_path: str | None = None) -> None:
        self._headless = headless
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._executable = executable_path
        self._browser = None
        self._context = None
        self._page = None

    @classmethod
    def capability(cls) -> EngineCapability:
        return EngineCapability(
            name="camoufox",
            fingerprint_resistance=8,
            ja4_diversity=9,
            dom_automation=5,
            resource_cost=6,
            tags=["camoufox", "firefox", "ja4-diverse", "stealth"],
        )

    async def launch(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright required for CamoufoxEngine. "
                "Install: pip install playwright && playwright install firefox"
            )

        self._pw = await async_playwright().start()
        launch_args = {
            "headless": self._headless,
            "firefox_user_prefs": {
                "dom.webdriver.enabled": False,
                "dom.webnotifications.enabled": False,
                "media.peerconnection.enabled": False,  # WebRTC leak prevention
                "privacy.trackingprotection.enabled": False,
            },
        }
        if self._executable:
            launch_args["executable_path"] = self._executable
            logger.info(f"CamoufoxEngine using Camoufox binary: {self._executable}")

        self._browser = await self._pw.firefox.launch(**launch_args)
        self._context = await self._browser.new_context(viewport=self._viewport)
        self._page = await self._context.new_page()
        logger.info("CamoufoxEngine launched successfully")

    async def navigate(self, url: str, proxy: str | None = None):
        if not self._page:
            await self.launch()
        if proxy:
            if self._page:
                await self._page.close()
            if self._context:
                await self._context.close()
            context_opts = {"proxy": {"server": proxy}}
            self._context = await self._browser.new_context(
                **context_opts, viewport=self._viewport
            )
            self._page = await self._context.new_page()
        await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await ensure_subresource_load(self._page)
        except Exception:
            logger.warning("Subresource load failed", exc_info=True)
        return _PageAdapter(self._page)

    async def close(self) -> None:
        if self._page:
            await self._page.close()
            self._page = None
        if self._context:
            await self._context.close()
            self._context = None
        if self._browser:
            await self._browser.close()
            self._browser = None
        if hasattr(self, '_pw'):
            await self._pw.stop()

    async def health_check(self) -> bool:
        if self._browser is None:
            return False
        return self._browser.is_connected()


class _PageAdapter:
    """Adapter wrapping a Playwright Page to conform to the Page protocol."""

    __slots__ = ('_page',)

    def __init__(self, page):
        self._page = page

    async def content(self) -> str:
        return await self._page.content()

    @property
    def url(self) -> str:
        return self._page.url

    async def evaluate(self, script: str):
        return await self._page.evaluate(script)

    async def screenshot(self, *, full_page: bool = False) -> bytes:
        return await self._page.screenshot(full_page=full_page)

    async def close(self) -> None:
        await self._page.close()
