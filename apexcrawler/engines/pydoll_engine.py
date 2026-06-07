"""PyDollEngine — CDP-native stealth browser engine via PyDoll.

No WebDriver = no navigator.webdriver flag. Built-in humanized interaction.
"""
from __future__ import annotations

import logging

from apexcrawler.engines.base import BaseEngine, EngineCapability
from apexcrawler.engines.subresource import ensure_subresource_load
from apexcrawler.routing.registry import EngineRegistry

logger = logging.getLogger(__name__)


async def _safe_execute_cdp(tab, cmd: str, params: dict | None = None):
    """Safely execute CDP command with fallback for missing _execute_cdp."""
    try:
        return await tab._execute_cdp(cmd, params or {})
    except AttributeError:
        logger.warning("PyDoll 版本不兼容，execute_cdp 失败 (cmd=%s)", cmd)
        return None


@EngineRegistry.register
class PyDollEngine(BaseEngine):
    """CDP-native stealth browser engine using PyDoll.

    PyDoll communicates directly via the Chrome DevTools Protocol (CDP),
    bypassing the WebDriver protocol entirely. This means no
    ``navigator.webdriver`` flag is ever set, giving excellent fingerprint
    resistance out of the box.

    Features:
    - No WebDriver dependency — pure CDP connection
    - Built-in humanized mouse/keyboard interactions
    - Low resource overhead compared to Playwright
    - Works with stealth JS injection for additional camouflage
    """

    def __init__(
        self,
        headless: bool = True,
        viewport: dict | None = None,
        humanize: bool = True,
    ):
        self._headless = headless
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._humanize = humanize
        self._browser = None
        self._tab = None
        self._running = False

    @classmethod
    def capability(cls) -> EngineCapability:
        return EngineCapability(
            name="pydoll",
            fingerprint_resistance=8,
            ja4_diversity=5,
            dom_automation=8,
            resource_cost=3,
            tags=["pydoll", "chromium", "cdp-native", "humanized", "stealth"],
        )

    async def launch(self) -> None:
        try:
            from pydoll.browser.chrome import Chrome
        except ImportError:
            raise ImportError(
                "PyDoll required for PyDollEngine. "
                "Install: pip install pydoll"
            )

        self._browser = Chrome(headless=self._headless)
        self._tab = await self._browser.start()

        # Inject DeviceProfile fingerprint via CDP
        from apexcrawler.fingerprint.consistency import get_profile

        profile = get_profile()
        await _safe_execute_cdp(self._tab, "Page.addScriptToEvaluateOnNewDocument", {
            "source": profile.cdp_inject_script(),
        })

        self._running = True
        logger.info("PyDollEngine launched successfully")

    async def navigate(
        self, url: str, proxy: str | None = None
    ) -> _PageAdapter:
        if not self._running:
            await self.launch()

        if proxy:
            logger.warning(
                "PyDollEngine proxy requires browser restart, ignoring proxy"
            )

        await self._tab.go_to(url)

        # Re-inject fingerprint script on every navigation
        from apexcrawler.fingerprint.consistency import get_profile

        profile = get_profile()
        await _safe_execute_cdp(self._tab, "Page.addScriptToEvaluateOnNewDocument", {
            "source": profile.cdp_inject_script(),
        })

        try:
            from ..engines.subresource import ensure_subresource_load
            # PyDoll Tab doesn't support Playwright's route() API,
            # so this is best-effort only
            await ensure_subresource_load(self._tab)
        except Exception:
            pass

        return _PageAdapter(self._tab)

    async def close(self) -> None:
        if self._browser:
            try:
                await self._browser.stop()
            except Exception:
                pass
            self._browser = None
            self._tab = None
            self._running = False

    async def health_check(self) -> bool:
        return self._running and self._browser is not None


class _PageAdapter:
    """Wraps PyDoll Tab to match Page protocol."""

    __slots__ = ('_tab',)

    def __init__(self, tab):
        self._tab = tab

    @property
    def url(self) -> str:
        return getattr(self._tab, 'url', '')

    @property
    def content(self):
        """Return coroutine for page HTML (property-style)."""
        return _safe_execute_cdp(self._tab, "Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "returnByValue": True,
        })

    async def evaluate(self, script: str):
        return await _safe_execute_cdp(self._tab, "Runtime.evaluate", {
            "expression": script,
            "returnByValue": True,
        })

    async def screenshot(self, *, full_page: bool = False) -> bytes:
        result = await _safe_execute_cdp(
            self._tab, "Page.captureScreenshot", {"format": "png"}
        )
        import base64
        return base64.b64decode(result.get("data", ""))

    async def close(self):
        await _safe_execute_cdp(self._tab, "Page.close", {})
