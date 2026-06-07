"""Cloudflare Turnstile automatic handler for Playwright-based engines.

References Scrapling's Cloudflare solver implementation for Turnstile
checkbox interaction and challenge resolution.

Usage:
    from apexcrawler.engines.turnstile_handler import handle_turnstile

    # After page navigation:
    await handle_turnstile(page, timeout=30000)
"""

from __future__ import annotations

import logging
import random
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page as AsyncPage
    from playwright.sync_api import Page as SyncPage

logger = logging.getLogger(__name__)

# Regex pattern matching Cloudflare challenge platform frames
__CF_FRAME_PATTERN__ = re.compile(
    r"^https?://challenges\.cloudflare\.com/cdn-cgi/challenge-platform/.*"
)

# Turnstile checkbox selectors for different challenge layouts
_BOX_SELECTORS = [
    "#cf_turnstile div",
    "#cf-turnstile div",
    ".turnstile>div>div",
    ".main-content p+div>div>div",
    "iframe[src*='challenges.cloudflare.com']",
    ".cf-turnstile",
]

# HTML title patterns indicating Cloudflare is active
_CF_TITLE_PATTERNS = [
    "Just a moment...",
    "Checking your browser",
    "Please complete the security check",
    "Attention Required! | Cloudflare",
]

# HTML content patterns indicating verification in progress
_VERIFY_PATTERNS = [
    "Verifying you are human.",
    "Checking your browser before accessing",
]


def _has_cf_title(html: str) -> bool:
    """Check if the page has a Cloudflare challenge title."""
    for title in _CF_TITLE_PATTERNS:
        if f"<title>{title}</title>" in html or title in html:
            return True
    return False


def _is_verifying(html: str) -> bool:
    """Check if the page is currently in verification state."""
    for pattern in _VERIFY_PATTERNS:
        if pattern in html:
            return True
    return False


def _detect_challenge_type(html: str) -> str | None:
    """Detect the type of Cloudflare Turnstile challenge.

    Returns one of: "non-interactive", "managed", "interactive", "embedded", None
    """
    challenge_types = ("non-interactive", "managed", "interactive")
    for ctype in challenge_types:
        if f"cType: '{ctype}'" in html:
            return ctype
    if "challenges.cloudflare.com/turnstile" in html:
        return "embedded"
    return None


async def handle_turnstile(page: "AsyncPage", timeout: int = 30000) -> bool:
    """Detect and solve Cloudflare Turnstile challenge on a page.

    This function:
    1. Waits for network idle
    2. Detects the Cloudflare challenge type
    3. For non-interactive: waits for automatic resolution
    4. For interactive/managed/embedded: locates and clicks the checkbox
    5. Waits for page redirect or challenge completion

    Args:
        page: Playwright async Page object.
        timeout: Maximum time to wait for challenge resolution (ms).

    Returns:
        True if the challenge was solved successfully, False otherwise.
    """
    # Wait for initial network idle
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    # Get current page HTML for challenge type detection
    html = await page.content()

    # Detect challenge type
    challenge_type = _detect_challenge_type(html)
    if not challenge_type:
        # Check if page title indicates a CF challenge
        if not _has_cf_title(html):
            logger.info("No Cloudflare challenge detected on this page")
            return True  # No challenge means success
        challenge_type = "non-interactive"

    logger.info(f"Cloudflare challenge detected: type={challenge_type}")

    # Handle non-interactive challenges (auto-resolve)
    if challenge_type == "non-interactive":
        return await _handle_non_interactive(page, timeout)

    # Handle interactive/managed/embedded challenges (checkbox click)
    return await _handle_interactive(page, challenge_type, timeout)


async def _handle_non_interactive(page: "AsyncPage", timeout: int) -> bool:
    """Handle non-interactive Cloudflare challenges that auto-resolve.

    For non-interactive challenges, Cloudflare validates the browser
    in the background and redirects automatically. We just need to wait.
    """
    import asyncio

    start_time = asyncio.get_event_loop().time()
    while True:
        elapsed = (asyncio.get_event_loop().time() - start_time) * 1000
        if elapsed > timeout:
            logger.warning("Non-interactive CF challenge timed out")
            return False

        html = await page.content()
        if not _has_cf_title(html):
            logger.info("Non-interactive Cloudflare challenge resolved")
            return True

        if "<title>Just a moment...</title>" not in html:
            # Non-interactive resolved without visible redirect
            logger.info("Cloudflare 'Just a moment' page disappeared")
            return True

        await asyncio.sleep(1)
        try:
            await page.wait_for_load_state()
        except Exception:
            pass


async def _handle_interactive(page: "AsyncPage", challenge_type: str, timeout: int) -> bool:
    """Handle interactive/managed/embedded Cloudflare challenges.

    Locates the Turnstile widget iframe and clicks the checkbox,
    then waits for the challenge to complete.
    """
    import asyncio

    html = await page.content()

    # Wait for verification spinner to disappear
    if challenge_type != "embedded":
        while _is_verifying(html):
            await asyncio.sleep(0.5)
            html = await page.content()

    # Try to locate the Turnstile iframe by URL pattern
    iframe = None
    for f in page.frames:
        if __CF_FRAME_PATTERN__.match(f.url):
            iframe = f
            break

    if iframe is not None:
        # Wait for the iframe to be stable
        try:
            await page.wait_for_load_state()
        except Exception:
            pass

        # Wait for iframe to be visible
        if challenge_type != "embedded":
            while True:
                try:
                    frame_elem = await iframe.frame_element()
                    is_visible = await frame_elem.is_visible()
                    if is_visible:
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        # Get bounding box and click the checkbox
        try:
            frame_elem = await iframe.frame_element()
            outer_box = await frame_elem.bounding_box()
        except Exception:
            outer_box = None

        if outer_box:
            # Calculate click coordinates (center of checkbox area)
            click_x = outer_box["x"] + random.randint(26, 28)
            click_y = outer_box["y"] + random.randint(25, 27)

            await page.mouse.click(click_x, click_y, delay=random.randint(100, 200))

            # Wait for network idle after click
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
    else:
        # Iframe not found by URL, try DOM-based locator
        logger.info("Turnstile iframe not found by URL pattern, trying DOM locators")
        clicked = False
        for selector in _BOX_SELECTORS:
            try:
                locator = page.locator(selector).last
                if await locator.count() > 0:
                    box = await locator.bounding_box()
                    if box:
                        click_x = box["x"] + random.randint(26, 28)
                        click_y = box["y"] + random.randint(25, 27)
                        await page.mouse.click(click_x, click_y, delay=random.randint(100, 200))
                        clicked = True
                        try:
                            await page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            pass
                        break
            except Exception:
                continue

        if not clicked:
            # Check if challenge is already solved
            html = await page.content()
            if not _has_cf_title(html):
                logger.info("Cloudflare challenge already resolved")
                return True

            logger.warning("Could not locate Turnstile checkbox")
            return False

    # Wait for challenge to complete (page redirect or title change)
    if challenge_type != "embedded":
        attempts = 0
        while True:
            if attempts >= 100:  # ~10 seconds max
                logger.info("Cloudflare page didn't redirect after 10s, continuing...")
                break
            await asyncio.sleep(0.1)
            html = await page.content()
            if not _has_cf_title(html):
                logger.info("Cloudflare challenge resolved successfully")
                return True
            attempts += 1

    # Final stability wait
    try:
        await page.wait_for_load_state()
    except Exception:
        pass

    html = await page.content()
    if not _has_cf_title(html):
        logger.info("Cloudflare challenge resolved successfully")
        return True

    # If still present, try one more time recursively
    logger.info("Cloudflare challenge still present, retrying...")
    return await handle_turnstile(page, timeout)
