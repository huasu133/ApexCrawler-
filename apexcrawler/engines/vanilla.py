"""Vanilla engine — standard Playwright-based browser engine.

Uses the stock Playwright Chromium browser with default fingerprint.
This is the baseline engine with minimal stealth, suitable for
low-difficulty targets and quick development iterations.
"""

from __future__ import annotations

from playwright.async_api import async_playwright

import logging

from apexcrawler.core.exceptions import EngineError
from apexcrawler.core.protocols import Page
from apexcrawler.engines.base import BaseEngine, EngineCapability
from apexcrawler.engines.subresource import ensure_subresource_load
from apexcrawler.fingerprint.consistency import DeviceProfile
from apexcrawler.routing.registry import EngineRegistry

logger = logging.getLogger(__name__)


@EngineRegistry.register
class VanillaEngine(BaseEngine):
    """Playwright-powered Chromium engine with default fingerprint.

    Uses the stock Playwright API — no stealth patches, no fingerprint
    modifications. Fast, reliable, and suitable for low-security targets
    or local development.
    """

    def __init__(self, headless: bool = True, viewport: dict | None = None,
                 profile: DeviceProfile | None = None) -> None:
        self._headless = headless
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._profile = profile or DeviceProfile(name="default")
        self._playwright = None
        self._browser = None
        self._context = None
        self._running = False

    @classmethod
    def capability(cls) -> EngineCapability:
        return EngineCapability(
            name="vanilla",
            fingerprint_resistance=2,
            ja4_diversity=3,
            dom_automation=8,
            resource_cost=2,
            tags=["playwright", "chromium", "lightweight"],
        )

    async def launch(self) -> None:
        """Start Playwright and launch a Chromium browser instance."""
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._context = await self._browser.new_context(
                viewport=self._viewport,
                user_agent=self._profile.user_agent,
            )
            self._running = True
        except Exception as exc:
            raise EngineError("vanilla", str(exc)) from exc

    async def navigate(self, url: str, proxy: str | None = None) -> Page:
        """Navigate to a URL and return a page.

        If proxy is provided, creates a fresh context with proxy settings
        to avoid side effects on other pages.
        """
        if not self._running:
            raise EngineError("vanilla", "Engine not launched — call launch() first")

        if proxy:
            if self._context:
                await self._context.close()
            self._context = await self._browser.new_context(
                viewport=self._viewport,
                proxy={"server": proxy},
                user_agent=self._profile.user_agent,
            )
            page = await self._context.new_page()
        else:
            page = await self._context.new_page()

        await page.goto(url, wait_until="domcontentloaded")
        try:
            await ensure_subresource_load(page)
        except Exception:
            logger.warning("Subresource load failed", exc_info=True)
        # Wrapped in an adapter so the caller can close it cleanly.
        return _PageAdapter(page, owns_browser_context=(proxy is not None))

    async def close(self) -> None:
        """Close the browser and stop Playwright."""
        self._running = False
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None


class _PageAdapter:
    """Adapter wrapping a Playwright page to conform to the Page protocol."""

    def __init__(self, pw_page, *, owns_browser_context: bool = False) -> None:
        self._pw_page = pw_page
        self._owns_ctx = owns_browser_context

    async def content(self) -> str:
        return await self._pw_page.content() if hasattr(self._pw_page, "content") else ""

    @property
    def url(self) -> str:
        return getattr(self._pw_page, "url", "")

    async def evaluate(self, script: str):
        return await self._pw_page.evaluate(script)

    async def screenshot(self, *, full_page: bool = False):
        return await self._pw_page.screenshot(full_page=full_page)

    async def close(self) -> None:
        await self._pw_page.close()
        if self._owns_ctx:
            await self._pw_page.context.close()
