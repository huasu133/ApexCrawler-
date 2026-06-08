"""Cloaked engine — CloakBrowser stealth engine via Playwright.

CloakBrowser is the most advanced engine in the ApexCrawler arsenal. It
provides Chrome DevTools Protocol (CDP) level hiding, WASM interception,
WebGPU virtualization, and comprehensive fingerprint spoofing.

This engine uses Playwright to connect to a CloakBrowser Chromium binary,
providing CDP-level anti-detection capabilities. For full CloakBrowser
features (WASM interception, WebGPU virtualization), set the executable_path
to the CloakBrowser binary.

Status: INTEGRATED — Real implementation via Playwright + CloakBrowser binary.
"""

from __future__ import annotations

import asyncio
import logging

from apexcrawler.engines.base import BaseEngine, EngineCapability
from apexcrawler.engines.subresource import ensure_subresource_load
from apexcrawler.routing.registry import EngineRegistry

logger = logging.getLogger(__name__)


@EngineRegistry.register
class CloakedEngine(BaseEngine):
    """CloakBrowser-based maximum-stealth engine.

    Provides:
    - CDP (Chrome DevTools Protocol) message hiding via CloakBrowser binary
    - WASM module interception and modification via CloakBrowser SDK
    - WebGPU virtualization for fingerprint-consistent GPU rendering
    - Full fingerprint vector control with anti-bot bypass arguments

    Use CloakBrowser only for targets that have defeated all lower-tier
    engines — it has the highest resource cost.
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
            name="cloaked",
            fingerprint_resistance=10,
            ja4_diversity=10,
            dom_automation=3,
            resource_cost=10,
            supports_cdp_hide=True,
            supports_wasm_intercept=True,
            supports_webgpu=True,
            tags=["cloakbrowser", "chromium", "stealth", "wasm"],
        )

    async def launch(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright required for CloakedEngine. "
                "Install: pip install playwright && playwright install chromium"
            )

        self._pw = await async_playwright().start()
        launch_args = {
            "headless": self._headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        if self._executable:
            launch_args["executable_path"] = self._executable
            logger.info("CloakedEngine using CloakBrowser binary: %s", self._executable)

        self._browser = await self._pw.chromium.launch(**launch_args)
        self._context = await self._browser.new_context(viewport=self._viewport)
        self._page = await self._context.new_page()

        # Inject WASM interceptor for SIMD neutralization
        try:
            from apexcrawler.anti_font.wasm_interceptor import WASMInterceptor
        except ImportError as e:
            logger.warning("WASMInterceptor import failed: %s", e)
            WASMInterceptor = None

        if WASMInterceptor is not None:
            self._wasm_interceptor = WASMInterceptor()
            await self._wasm_interceptor.inject(self._page)
            logger.info("CloakedEngine launched successfully (WASM interceptor active)")
        else:
            self._wasm_interceptor = None
            logger.info("CloakedEngine launched successfully (WASM interceptor unavailable)")

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
            if self._wasm_interceptor is not None:
                await self._wasm_interceptor.inject(self._page)
        await self._page.goto(url, wait_until="networkidle", timeout=30000)
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

    @property
    def content(self):
        """Returns a coroutine for page HTML (property-style: await page.content)."""
        return self._page.content()

    @property
    def url(self) -> str:
        return self._page.url

    async def evaluate(self, script: str):
        return await self._page.evaluate(script)

    async def screenshot(self, *, full_page: bool = False) -> bytes:
        return await self._page.screenshot(full_page=full_page)

    async def close(self) -> None:
        await self._page.close()
