"""Concrete pipeline stage implementations.

Stages in order: Schedule → Route → Evade → Extract → Validate → Store
Each stage reads from and writes to PipelineContext.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.context import PipelineContext
from ..core.exceptions import (
    ConfigurationError,
    ExtractionError,
    NonRetryableError,
    NotSupportedError,
)
from ..http.tls_router import TLSRouter

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ScheduleStage:
    """Validates the target URL and enforces human-like scheduling delays.

    Uses a :class:`TimingScheduler` to compute inter-request delays
    that mimic real browsing cadence.  When no scheduler is provided
    the stage acts as a pass-through (backward-compatible).
    """

    name = "schedule"

    def __init__(self, timing: TimingScheduler | None = None) -> None:
        self._timing = timing

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.target_url:
            raise ConfigurationError("target_url is required")

        if self._timing is not None:
            delay = self._timing.compute_delay()
            logger.info(
                f"[schedule] trace={ctx.trace_id} computed_delay={delay:.2f}s"
                f" url={ctx.target_url}"
            )
            import asyncio
            await asyncio.sleep(delay)
        else:
            logger.info(f"[schedule] trace={ctx.trace_id} url={ctx.target_url}")

        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        # No side effects to undo
        pass


class RouteStage:
    """Selects an engine for the target URL via :class:`DecisionEngine`.

    Falls back to the cached :class:`_DefaultEngineMatcher` for URL-based
    host-keyword matching when no :class:`DecisionEngine` is injected.
    """

    name = "route"

    def __init__(
        self,
        matcher: object | None = None,
        engine: DecisionEngine | None = None,
    ) -> None:
        self._matcher = matcher
        self._engine = engine

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if self._engine is not None:
            result = await self._engine.recommend(ctx.target_url)
            ctx.selected_engine = result.get("entry_point", "http")
            ctx.route_reason = (
                f"DecisionEngine (entry={result.get('entry_point', 'http')},"
                f" confidence={result.get('confidence', 0.5):.2f})"
            )
            ctx.target_difficulty = int(result.get("confidence", 0.5) * 10)
        else:
            matcher = self._matcher or _DefaultEngineMatcher()
            result = matcher.match(ctx.target_url)
            ctx.selected_engine = result.engine
            ctx.route_reason = result.reason
            ctx.target_difficulty = result.difficulty

        logger.info(
            f"[route] trace={ctx.trace_id} engine={ctx.selected_engine} "
            f"reason={ctx.route_reason} difficulty={ctx.target_difficulty}"
        )
        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.selected_engine = ""
        ctx.route_reason = ""
        ctx.target_difficulty = 0


class EvadeStage:
    """Assigns proxy, User-Agent, TLS profile, and device fingerprint to evade detection."""

    name = "evade"

    def __init__(
        self,
        router: TLSRouter | None = None,
        proxies: list[str] | None = None,
        device_profile: "DeviceProfile | None" = None,
    ):
        self._router = router or TLSRouter()
        self._proxies = proxies or []
        self._proxy_idx = 0
        if device_profile is None:
            from ..fingerprint.consistency import DEVICE_PROFILES
            self._device_profile = DEVICE_PROFILES[0]
        else:
            self._device_profile = device_profile

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        # Select TLS profile with rotation
        profile = self._router.rotate()
        if profile is None:
            raise ConfigurationError("TLSRouter.rotate() returned no available profile")
        ctx.user_agent = profile.ua
        ctx.tls_profile = profile.name
        ctx.ja4_fingerprint = profile.ja4_prefix
        ctx.browser_profile = {
            "ja4": profile.ja4_prefix,
            "alpn": profile.alpn,
            "platform": profile.platform,
        }

        # Assign proxy if available
        if self._proxies:
            ctx.proxy = self._proxies[self._proxy_idx % len(self._proxies)]
            self._proxy_idx += 1

        # Fill device fingerprint attributes from DeviceProfile
        dp = self._device_profile
        ctx.webgl_renderer = dp.webgl_renderer
        ctx.canvas_hash = hashlib.sha256(
            f"{dp.name}:{dp.webgl_renderer}".encode()
        ).hexdigest()[:16]
        ctx.audio_fingerprint = hashlib.sha256(
            f"audio:{dp.name}:{dp.platform}".encode()
        ).hexdigest()[:16]
        ctx.fonts = [
            "Arial", "Times New Roman", "Courier New", "Georgia",
            "Verdana", "Trebuchet MS", "Comic Sans MS",
        ]

        logger.info(
            f"[evade] trace={ctx.trace_id} profile={profile.name} "
            f"proxy={ctx.proxy or 'none'} ja4={profile.ja4_prefix} "
            f"device={dp.name}"
        )
        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.proxy = ""
        ctx.user_agent = ""
        ctx.tls_profile = ""
        ctx.ja4_fingerprint = ""
        ctx.browser_profile = {}
        ctx.webgl_renderer = ""
        ctx.canvas_hash = ""
        ctx.audio_fingerprint = ""
        ctx.fonts = []


class ExtractStage:
    """Navigates to target URL and extracts content.

    Two modes:
    1. HTTP mode (fast): httpx direct request, optionally routed through
       a :class:`ConnectionReuseManager`-managed proxy for connection reuse.
    2. Browser mode (full): Playwright/CloakBrowser via engine pool
    """

    name = "extract"

    def __init__(self, engine_factory=None, validator: "CrossValidator | None" = None,
                 mobile_sniffer: "MobileAPISniffer | None" = None,
                 conn_manager: "ConnectionReuseManager | None" = None,
                 sel_healer: "SelHealer | None" = None,
                 cleaner: "Cleaner | None" = None):
        self._engine_factory = engine_factory
        from ..extraction.cross_validator import CrossValidator as CV
        from ..routing.mobile_sniffer import MobileAPISniffer as MS
        self._validator = validator or CV()
        self._mobile_sniffer = mobile_sniffer or MS()
        self._conn_manager = conn_manager
        from ..extraction.sel_healer import SelHealer as SH
        from ..extraction.cleaner import Cleaner as CL
        self._sel_healer = sel_healer or SH()
        self._cleaner = cleaner or CL()

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.target_url:
            raise ConfigurationError("No target URL")

        logger.info(
            f"[extract] trace={ctx.trace_id} engine={ctx.selected_engine or 'http'} "
            f"url={ctx.target_url}"
        )

        # Step 1: Try lightweight HTTP (includes mobile API probe)
        self._api_detected = False
        html = await self._try_http(ctx)
        if html and len(html) > 200:
            ctx.raw_html = html
            # Apply Cleaner post-extraction
            try:
                ctx.raw_html = self._cleaner.clean(html)
                logger.debug(f"[extract] cleaner applied, {len(ctx.raw_html)} bytes")
            except Exception:
                pass
            # Use higher confidence for API/JSON responses
            if self._api_detected:
                ctx.extraction_confidence = 0.8
            else:
                ctx.extraction_confidence = 0.7
            return ctx

        # Step 2: Fall back to browser
        html = await self._try_browser(ctx)
        if html:
            try:
                ctx.raw_html = self._cleaner.clean(html)
            except Exception:
                ctx.raw_html = html
                logger.warning("[extract] cleaner failed, using raw html")
            ctx.extraction_confidence = 0.5
            return ctx

        # Step 3: SelHealer — try self-healing if extraction failed
        try:
            healed = await self._sel_healer.heal(ctx.target_url, ctx)
            if healed:
                ctx.raw_html = healed
                ctx.extraction_confidence = 0.4
                logger.info(f"[extract] sel_healer recovered content")
                return ctx
        except Exception as e:
            logger.debug(f"[extract] sel_healer failed: {e}")

        raise ExtractionError(detail=f"Failed to fetch {ctx.target_url}")

    async def _try_http(self, ctx: PipelineContext) -> str | None:
        # Step 0: Probe mobile/API endpoints before hitting the full page
        mobile_endpoint = await self._mobile_sniffer.probe(ctx.target_url)
        fetch_url = mobile_endpoint.url if mobile_endpoint else ctx.target_url
        is_api = mobile_endpoint is not None and mobile_endpoint.confidence >= 0.9

        if mobile_endpoint:
            logger.info(
                f"[extract] mobile_sniffer → {fetch_url} "
                f"(confidence={mobile_endpoint.confidence:.1f}, source={mobile_endpoint.source})"
            )

        try:
            import httpx
            from urllib.parse import urlparse, urlunparse
            from ..utils.dns_cache import dns_cache

            target_url = fetch_url
            headers = {"User-Agent": ctx.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}

            # DNS cache: resolve host to IP for faster connection (HTTP only; HTTPS needs hostname for SSL)
            parsed = urlparse(target_url)
            host = parsed.netloc.split(":")[0]
            resolved_ip = dns_cache.resolve(host)
            if resolved_ip != host and parsed.scheme == "http":
                netloc = parsed.netloc.replace(host, resolved_ip)
                target_url = urlunparse(parsed._replace(netloc=netloc))
                headers["Host"] = host

            # Connection reuse: obtain proxy URL from ConnectionReuseManager
            proxy = None
            if self._conn_manager is not None:
                proxy = await self._conn_manager.get_proxy(ctx.target_url)

            async with httpx.AsyncClient(
                timeout=15, follow_redirects=True,
                proxy=proxy, headers=headers,
            ) as c:
                r = await c.get(target_url)
                ctx._last_status = r.status_code
                r.raise_for_status()
                html = r.text

            # Brotli decompression support
            if r.headers.get("Content-Encoding") == "br":
                from ..utils.brotli_support import decompress_brotli
                raw = r.content
                decompressed = decompress_brotli(raw)
                if decompressed:
                    html = decompressed.decode("utf-8", errors="replace")
                    logger.debug(f"[extract] brotli decompressed {len(raw)} → {len(html)} bytes")

            # If we got JSON from a mobile API, use it directly — skip browser fallback
            if is_api:
                self._api_detected = True
                ctx.extraction_confidence = 0.8
                logger.info(
                    f"[extract] mobile API response received, confidence=0.8"
                )
                return html

            # Cross-validate common fields from HTML
            try:
                for field in ["title", "description", "price", "name", "author"]:
                    result = await self._validator.validate(html, field)
                    if result["confidence"] > 0.5:
                        logger.debug(
                            f"[extract] cross_validator field={field} "
                            f"value={result['value']} "
                            f"confidence={result['confidence']:.2f} "
                            f"sources_agree={result['sources_agree']}"
                        )
            except Exception:
                logger.debug("cross_validator skipped (non-critical)")

            return html
        except Exception as e:
            logger.warning(f"[extract] HTTP failed: {e}")
            return None

    async def _try_browser(self, ctx: PipelineContext) -> str | None:
        if not self._engine_factory or not ctx.selected_engine:
            return None
        try:
            async with self._engine_factory.acquire(ctx.selected_engine) as engine:
                page = await engine.navigate(ctx.target_url, proxy=ctx.proxy or None)
                html = await page.content
                await page.close()
                return html
        except Exception as e:
            logger.warning(f"[extract] Browser failed: {e}")
            return None

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.raw_html = ""
        ctx.extracted_data = None
        ctx.extraction_confidence = 0.0


class ValidateStage:
    """Validates extracted data against the target schema."""

    name = "validate"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        ctx.validation_errors = []

        if ctx.extracted_data is None:
            logger.info(
                f"[validate] trace={ctx.trace_id} no extracted data to validate "
                f"(confidence={ctx.extraction_confidence:.2f})"
            )
            ctx.validation_passed = False
            return ctx

        schema = ctx.extraction_schema
        if schema is None:
            logger.info(f"[validate] trace={ctx.trace_id} no schema — pass-through")
            ctx.validation_passed = True
            return ctx

        # Attempt schema validation (Pydantic-style)
        try:
            if hasattr(schema, "model_validate"):
                schema.model_validate(ctx.extracted_data)
            elif hasattr(schema, "parse_obj"):
                schema.parse_obj(ctx.extracted_data)  # type: ignore
            ctx.validation_passed = True
        except Exception as e:
            ctx.validation_errors.append(str(e))
            ctx.validation_passed = False
            logger.warning(
                f"[validate] trace={ctx.trace_id} validation errors: {ctx.validation_errors}"
            )

        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.validation_passed = False
        ctx.validation_errors = []


class StoreStage:
    """Persists the crawl result.

    For now, logs the result and writes a stored_id to the context.
    In production this would write to a database, object store, or queue.
    """

    name = "store"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        # Generate a deterministic stored_id from trace + url
        key = f"{ctx.trace_id}:{ctx.target_url}"
        ctx.stored_id = hashlib.sha256(key.encode()).hexdigest()[:16]

        logger.info(
            f"[store] trace={ctx.trace_id} stored_id={ctx.stored_id} "
            f"engine={ctx.selected_engine} duration={ctx.duration():.2f}s "
            f"valid={ctx.validation_passed}"
        )
        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.stored_id = ""


# ── Internal helpers ──────────────────────────────────────────────


class FontDecodeStage:
    """Decodes font-encoded text using FontCracker + OCREngine + DomFixer.

    Runs after extraction to decode any font-obfuscated content (e.g. 58同城, 猫扑).
    Falls back through FontTools → OCR → shape matching.
    """

    name = "font_decode"

    def __init__(self, font_cracker=None, ocr_engine=None, dom_fixer=None):
        from ..anti_font.font_cracker import FontCracker as FC
        from ..anti_font.ocr_engine import OCREngine
        from ..anti_font.dom_fixer import DomFixer
        self._cracker = font_cracker or FC()
        self._ocr = ocr_engine or OCREngine()
        self._fixer = dom_fixer or DomFixer()

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.raw_html or "@font-face" not in ctx.raw_html:
            logger.debug(f"[font_decode] no encoded fonts detected, skipping")
            return ctx

        try:
            # Step 1: Fix DOM CSS position offsets
            fixed_html = self._fixer.fix(ctx.raw_html)
            ctx.raw_html = fixed_html
            logger.info(f"[font_decode] DOMFixer applied")

            # Step 2: Try FontTools glyph mapping
            decoded = await self._cracker.decode(ctx.raw_html)
            if decoded and decoded != ctx.raw_html:
                ctx.raw_html = decoded
                ctx.extraction_confidence = max(ctx.extraction_confidence or 0, 0.85)
                logger.info(f"[font_decode] FontTools decoded successfully")
                return ctx

            # Step 3: OCR fallback for dynamic glyphs
            ocr_result = await self._ocr.decode(ctx.raw_html)
            if ocr_result and ocr_result.confidence > 0.7:
                ctx.raw_html = ocr_result.text
                ctx.extraction_confidence = max(ctx.extraction_confidence or 0, ocr_result.confidence)
                logger.info(
                    f"[font_decode] OCR decoded (confidence={ocr_result.confidence:.2f})"
                )
        except Exception as e:
            logger.warning(f"[font_decode] failed: {e}")

        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        pass


# ── Internal helpers ──────────────────────────────────────────────


@dataclass
class _MatchResult:
    engine: str
    reason: str
    difficulty: int


class _DefaultEngineMatcher:
    """Default engine matcher used when no custom matcher is injected.

    Maps URL patterns to engine names based on known anti-bot targets.
    """

    _RULES: list[tuple[str, str, int]] = [
        # (host keyword, engine, difficulty 1–10)
        ("cloudflare", "camoufox", 10),
        ("akamai", "camoufox", 9),
        ("datadome", "camoufox", 9),
        ("imperva", "playwright", 8),
        ("incapsula", "playwright", 8),
        ("shopify", "playwright", 6),
        ("twitter.com", "playwright", 5),
        ("x.com", "playwright", 5),
        ("linkedin.com", "playwright", 5),
        ("facebook.com", "playwright", 4),
        ("google.com", "playwright", 3),
    ]

    def match(self, url: str) -> _MatchResult:
        url_lower = url.lower()
        for keyword, engine, difficulty in self._RULES:
            if keyword in url_lower:
                return _MatchResult(engine=engine, reason=f"matched '{keyword}'", difficulty=difficulty)
        # Default fallback
        return _MatchResult(engine="httpx", reason="default", difficulty=1)
