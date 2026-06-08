"""Camoufox engine — Camoufox stealth browser engine via Playwright (Firefox).

Camoufox is a Firefox fork with deep fingerprint randomization at the
browser level, including JA4/TLS fingerprint diversity, Canvas/WebGL/
AudioContext spoofing, and font enumeration protection.

This engine uses Playwright's Firefox channel to connect to the Camoufox
binary. For full Camoufox SDK features (humanize, geoip, font spoofing),
set the executable_path to the Camoufox binary.

Enhanced with:
- Comprehensive Firefox user preferences for realistic fingerprint
- Cloudflare Turnstile auto-handling
- Configurable Turnstile resolution option

Status: INTEGRATED — Real implementation via Playwright + Camoufox binary.
"""

from __future__ import annotations

import logging

from apexcrawler.engines.base import BaseEngine, EngineCapability
from apexcrawler.engines.subresource import ensure_subresource_load
from apexcrawler.routing.registry import EngineRegistry

logger = logging.getLogger(__name__)

# Comprehensive Firefox user preferences for realistic browser fingerprint.
# These prefs mimic a real Firefox installation to avoid detection.
_FIREFOX_FINGERPRINT_PREFS = {
    # ── Automation detection prevention ──
    "dom.webdriver.enabled": False,
    "dom.webnotifications.enabled": False,

    # ── WebRTC leak prevention ──
    "media.peerconnection.enabled": False,
    "media.peerconnection.ice.default_address_only": True,
    "media.peerconnection.ice.no_host": True,
    "media.peerconnection.ice.relay_only": False,
    "media.peerconnection.use_document_iceservers": False,

    # ── Tracking & fingerprinting protection ──
    "privacy.trackingprotection.enabled": False,
    "privacy.trackingprotection.fingerprinting.enabled": False,
    "privacy.trackingprotection.cryptomining.enabled": False,
    "privacy.firstparty.isolate": True,
    "privacy.resistFingerprinting": True,         # Firefox's built-in RFP — enabled for layered protection

    # ── Advanced fingerprint protection ──
    "privacy.fingerprintingProtection": True,     # Firefox 130+ native protection
    "privacy.fingerprintingProtection.pbmode": True,

    # ── Canvas & WebGL ──
    "webgl.disabled": False,        # Ensure WebGL is available (do not disable!)
    "canvas.capture.enabled": False, # Prevent Canvas screenshot
    "webgl.enable-webgl2": True,

    # ── WebRTC leak prevention (enhanced) ──
    "media.peerconnection.ice.obfuscate_host_addresses": True,
    "media.peerconnection.ice.proxy_only_if_behind_proxy": True,
    "media.peerconnection.turn.disable": True,

    # ── Network & cache ──
    "network.http.referer.XOriginPolicy": 2,       # Strict referer policy
    "network.http.referer.trimmingPolicy": 2,
    "network.cookie.cookieBehavior": 0,            # Allow all cookies
    "network.cookie.lifetimePolicy": 0,            # Cookies persist until expiry
    "network.IDN_show_punycode": False,

    # ── Performance fingerprinting ──
    "dom.enable_performance": True,
    "dom.enable_resource_timing": True,

    # ── Font fingerprint protection ──
    "layout.css.font-visibility.level": 1,         # Limit font enumeration

    # ── Telemetry & data collection ──
    "toolkit.telemetry.enabled": False,
    "toolkit.telemetry.unified": False,
    "toolkit.telemetry.archive.enabled": False,
    "datareporting.healthreport.uploadEnabled": False,
    "datareporting.policy.dataSubmissionEnabled": False,

    # ── Network & DNS ──
    "network.dns.disablePrefetch": True,
    "network.prefetch-next": False,
    "network.http.speculative-parallel-limit": 0,
    "network.predictor.enabled": False,
    "network.predictor.enable-prefetch": False,

    # ── DOM & rendering ──
    "dom.battery.enabled": False,
    "dom.gamepad.enabled": False,
    "dom.vr.enabled": False,
    "dom.webaudio.enabled": True,
    "media.video_stats.enabled": False,

    # ── Security & certificates ──
    "security.ssl.enable_ocsp_stapling": True,
    "security.ssl.enable_ocsp_must_staple": True,
    "security.cert_pinning.enforcement_level": 2,

    # ── Session & cache ──
    "browser.sessionhistory.max_entries": 50,
    "browser.sessionstore.restore_on_demand": False,
    "network.http.use-cache": True,
}


@EngineRegistry.register
class CamoufoxEngine(BaseEngine):
    """Camoufox-based stealth engine (Firefox-based).

    Provides:
    - Realistic JA4/TLS fingerprint randomization via Camoufox binary
    - Canvas, WebGL, AudioContext spoofing
    - Font enumeration protection
    - Navigator property normalization
    - WebRTC leak prevention
    - Cloudflare Turnstile auto-handling (optional, enabled by default)

    Camoufox is the recommended engine for medium-high difficulty targets
    where Playwright-based engines are detected, especially Cloudflare
    protected sites.
    """

    def __init__(self, headless: bool = True, viewport: dict | None = None,
                 executable_path: str | None = None,
                 solve_turnstile: bool = True) -> None:
        self._headless = headless
        self._viewport = viewport or {"width": 1920, "height": 1080}
        self._executable = executable_path
        self._solve_turnstile = solve_turnstile
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
            tags=["camoufox", "firefox", "ja4-diverse", "stealth", "cloudflare"],
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
            "firefox_user_prefs": dict(_FIREFOX_FINGERPRINT_PREFS),
        }
        if self._executable:
            launch_args["executable_path"] = self._executable
            logger.info("CamoufoxEngine using Camoufox binary: %s", self._executable)

        self._browser = await self._pw.firefox.launch(**launch_args)

        # Create context with realistic Firefox UA
        context_opts = {
            "viewport": self._viewport,
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) "
                "Gecko/20100101 Firefox/128.0"
            ),
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "permissions": ["geolocation"],
            "color_scheme": "dark",
            "device_scale_factor": 2,
        }
        self._context = await self._browser.new_context(**context_opts)
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

        # Cloudflare Turnstile auto-handling
        if self._solve_turnstile:
            try:
                from apexcrawler.engines.turnstile_handler import handle_turnstile
                await handle_turnstile(self._page, timeout=30000)
            except Exception as e:
                logger.warning("Turnstile handling failed: %s", e)

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
