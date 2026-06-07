"""CloakedV2 engine — CloakBrowser stealth engine with enhanced fingerprint control.

CloakBrowser provides the most advanced anti-detection capabilities with
JA4/TLS diversity at level 12, fingerprint resistance at level 12, CDP-level
hiding, WASM interception, and WebGPU virtualization.

This engine uses CloakBrowser's launch_async() API which returns a standard
Playwright Browser object with C++-level fingerprint patches applied.

Status: INTEGRATED — Real implementation via CloakBrowser async API.
"""

from __future__ import annotations

import logging

from apexcrawler.engines.base import BaseEngine, EngineCapability
from apexcrawler.engines.subresource import ensure_subresource_load
from apexcrawler.routing.registry import EngineRegistry

logger = logging.getLogger(__name__)


@EngineRegistry.register
class CloakedV2Engine(BaseEngine):
    """CloakBrowser maximum-stealth engine (enhanced fingerprint control).

    Provides:
    - 49 C++-level Chromium patches (Canvas, WebGL, AudioContext, WebRTC, fonts, navigator)
    - CDP message hiding via CloakBrowser binary
    - WASM module interception and modification
    - WebGPU virtualization for fingerprint-consistent GPU rendering
    - Full fingerprint vector control with anti-bot bypass arguments
    - DeviceProfile init_script injection for consistent browser fingerprint

    Use CloakedV2Engine for the most difficult targets that have defeated
    all lower-tier engines, including CloakedEngine v1.
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
            name="cloaked_v2",
            fingerprint_resistance=12,
            ja4_diversity=12,
            dom_automation=3,
            resource_cost=10,
            supports_cdp_hide=True,
            supports_wasm_intercept=True,
            supports_webgpu=True,
            tags=["cloakbrowser", "chromium", "stealth", "wasm", "v2"],
        )

    async def launch(self) -> None:
        try:
            import cloakbrowser
        except ImportError:
            raise ImportError(
                "CloakBrowser required for CloakedV2Engine. "
                "Install: pip install cloakbrowser"
            )

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
            logger.info(f"CloakedV2Engine using CloakBrowser binary: {self._executable}")

        self._browser = await cloakbrowser.launch_async(**launch_args)
        self._context = await self._browser.new_context(viewport=self._viewport)
        self._page = await self._context.new_page()

        # Inject DeviceProfile init script for consistent fingerprint
        from apexcrawler.fingerprint.consistency import DeviceProfile
        profile = DeviceProfile(name="cloaked_v2_default")
        await self._context.add_init_script(profile.cdp_inject_script())

        # Inject WASM interceptor for SIMD neutralization
        from ..anti_font.wasm_interceptor import WASMInterceptor
        self._wasm_interceptor = WASMInterceptor()
        await self._wasm_interceptor.inject(self._page)

        logger.info("CloakedV2Engine launched successfully (CloakBrowser + WASM interceptor + fingerprint injection active)")

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
            # Re-inject WASM interceptor for new context
            await self._wasm_interceptor.inject(self._page)
            # Re-inject DeviceProfile init script for new context
            from apexcrawler.fingerprint.consistency import DeviceProfile
            profile = DeviceProfile(name="cloaked_v2_default")
            await self._context.add_init_script(profile.cdp_inject_script())
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

    async def health_check(self) -> bool:
        if self._browser is None:
            return False
        return self._browser.is_connected()


class _PageAdapter:
    """Adapter wrapping a CloakBrowser/Playwright Page to conform to the Page protocol."""

    __slots__ = ('_page',)

    def __init__(self, page):
        self._page = page

    @property
    def content(self):
        """Returns a coroutine that resolves to page HTML.

        Usage: html = await page.content  (property-style access)
        """
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
