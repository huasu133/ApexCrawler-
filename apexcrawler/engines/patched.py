"""Patched engine — PatchRight-based stealth browser engine via Playwright.

PatchRight is a MonkeyPatch version of Playwright that injects stealth
patches into the Chromium rendering engine at runtime, modifying navigator
properties, WebDriver flags, and other browser-leak vectors.

This engine uses standard Playwright Chromium with anti-detection arguments
and optional playwright-stealth integration. For full PatchRight SDK features,
install the 'patchright' package and point executable_path to it.

Status: INTEGRATED — Real implementation via Playwright + stealth patches.
"""

from __future__ import annotations

import logging

from apexcrawler.engines.base import BaseEngine, EngineCapability
from apexcrawler.engines.subresource import ensure_subresource_load
from apexcrawler.routing.registry import EngineRegistry

logger = logging.getLogger(__name__)

_STEALTH_JS = """
// Remove webdriver detection
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
// Fix chrome object
window.chrome = {runtime: {}};
// Fix permissions
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
    Promise.resolve({state: Notification.permission}) :
    originalQuery(parameters)
);
// Fix plugins — return Plugin-like objects, not numbers
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const createPlugin = (name, filename, description) => {
            const plugin = {
                name: name,
                filename: filename,
                description: description,
                length: 2,
            };
            plugin[0] = {type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: plugin};
            plugin[1] = {type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: plugin};
            plugin.item = function(n) { return this[n] || null; };
            plugin.namedItem = function(name) { return null; };
            return plugin;
        };
        return [
            createPlugin('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format'),
            createPlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', ''),
            createPlugin('Native Client', 'internal-nacl-plugin', ''),
        ];
    },
});
// Fix languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en'],
});
"""


@EngineRegistry.register
class PatchedEngine(BaseEngine):
    """PatchRight-based stealth engine (Chromium + stealth JS injection).

    Provides:
    - DOM-level patches to hide automation indicators (navigator.webdriver, etc.)
    - Stealth JavaScript injection via add_init_script
    - Anti-detection Chromium launch arguments
    - Optional playwright-stealth / patchright SDK integration
    """

    def __init__(self, headless: bool = True, viewport: dict | None = None,
                 executable_path: str | None = None,
                 use_stealth_js: bool = True,
                 use_undetected: bool = False) -> None:
        self._headless = headless
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._executable = executable_path
        self._use_stealth_js = use_stealth_js
        self._use_undetected = use_undetected
        self._browser = None
        self._context = None
        self._page = None

    @classmethod
    def capability(cls) -> EngineCapability:
        return EngineCapability(
            name="patched",
            fingerprint_resistance=5,
            ja4_diversity=4,
            dom_automation=7,
            resource_cost=4,
            tags=["patchright", "chromium", "dom-patch"],
        )

    async def launch(self) -> None:
        if self._use_undetected:
            try:
                from .patched_undetected import (
                    create_undetected_browser,
                    UndetectedBrowserError,
                )
                result = await create_undetected_browser(
                    headless=self._headless,
                    stealth_js=_STEALTH_JS if self._use_stealth_js else None,
                )
                self._browser = result["browser"]
                self._context = result["context"]
                self._page = result["page"]
                logger.info("PatchedEngine launched with undetected-chromedriver")
                return
            except (ImportError, UndetectedBrowserError) as e:
                logger.warning(
                    "undetected-chromedriver unavailable, falling back to Playwright: %s",
                    e,
                )

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError(
                "Playwright required for PatchedEngine. "
                "Install: pip install playwright && playwright install chromium"
            )

        self._pw = await async_playwright().start()
        launch_args = {
            "headless": self._headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-features=IsolateOrigins,site-per-process",
                "--disable-features=TranslateUI",
                "--mute-audio",
                "--disable-component-extensions-with-background-pages",
            ],
        }
        if self._executable:
            launch_args["executable_path"] = self._executable
            logger.info(f"PatchedEngine using PatchRight binary: {self._executable}")

        self._browser = await self._pw.chromium.launch(**launch_args)
        self._context = await self._browser.new_context(viewport=self._viewport)

        if self._use_stealth_js:
            await self._context.add_init_script(_STEALTH_JS)

        self._page = await self._context.new_page()
        logger.info("PatchedEngine launched successfully")

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
            if self._use_stealth_js:
                await self._context.add_init_script(_STEALTH_JS)
            self._page = await self._context.new_page()
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

    @property
    def keyboard(self):
        return self._page.keyboard

    @property
    def mouse(self):
        return self._page.mouse

    async def evaluate(self, script: str):
        return await self._page.evaluate(script)

    async def screenshot(self, *, full_page: bool = False) -> bytes:
        return await self._page.screenshot(full_page=full_page)

    async def close(self) -> None:
        await self._page.close()
