"""
PatchedEngine undetected-chromedriver backend.
Provides Playwright-like interface mapping for uc.Chrome.
"""
import asyncio
import functools
import logging
from typing import Any

logger = logging.getLogger(__name__)


class UndetectedBrowserError(RuntimeError):
    """Raised when undetected-chromedriver is required but not installed."""


class _UndetectedContext:
    """Mimics Playwright BrowserContext for undetected-chromedriver pages."""

    __slots__ = ('_driver', '_init_script')

    def __init__(self, driver):
        self._driver = driver
        self._init_script = None

    async def add_init_script(self, script: str):
        self._init_script = script


class _UndetectedPage:
    """Wraps a Selenium WebDriver page to match Playwright's Page minimal interface."""

    __slots__ = ('_driver', '_init_script')

    def __init__(self, driver):
        self._driver = driver
        self._init_script = None

    @property
    def url(self) -> str:
        return self._driver.current_url

    async def goto(self, url: str, wait_until: str = "networkidle", timeout: int = 30000):
        self._driver.get(url)
        # Execute CDP-level init script if provided via CDP
        if hasattr(self._driver, 'execute_cdp_cmd') and self._init_script:
            try:
                self._driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
                    "source": self._init_script,
                })
            except Exception:
                pass

    @property
    def content(self):
        """Returns a coroutine that resolves to page HTML (property-style access)."""
        fut = asyncio.get_event_loop().run_in_executor(
            None, lambda: self._driver.page_source
        )
        return fut

    async def evaluate(self, script: str):
        return self._driver.execute_script(script)

    async def close(self) -> None:
        if self._driver:
            try:
                self._driver.quit()
            except Exception as e:
                logger.debug("Undetected driver quit error: %s", e)
            self._driver = None


async def create_undetected_browser(
    headless: bool = True,
    stealth_js: str | None = None,
    **kwargs,
) -> dict:
    """Create undetected-chromedriver instance.

    Returns dict with 'browser', 'context', 'page'.

    Raises:
        UndetectedBrowserError: If undetected-chromedriver is not installed.
    """
    try:
        import undetected_chromedriver as uc
    except ImportError:
        raise UndetectedBrowserError(
            "undetected-chromedriver not installed. "
            "pip install undetected-chromedriver"
        )

    options = uc.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    loop = asyncio.get_running_loop()
    driver = await loop.run_in_executor(
        None,
        functools.partial(uc.Chrome, options=options, headless=headless, use_subprocess=True),
    )
    context = _UndetectedContext(driver)
    page = _UndetectedPage(driver)

    if stealth_js:
        context._init_script = stealth_js

    return {"browser": driver, "context": context, "page": page}
