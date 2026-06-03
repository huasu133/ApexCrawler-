"""Concrete pipeline stage implementations.

Stages in order: Schedule → Route → Evade → Extract → Validate → Store
Each stage reads from and writes to PipelineContext.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from ..core.context import PipelineContext
from ..core.exceptions import (
    ConfigurationError,
    ExtractionError,
    NonRetryableError,
    NotSupportedError,
)
from ..http.tls_router import TLSRouter

logger = logging.getLogger(__name__)


class ScheduleStage:
    """Pass-through stage that collects the task from the scheduler.

    Responsible for validating that the target URL is present and well-formed.
    """

    name = "schedule"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.target_url:
            raise ConfigurationError("target_url is required")
        logger.info(f"[schedule] trace={ctx.trace_id} url={ctx.target_url}")
        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        # No side effects to undo
        pass


class RouteStage:
    """Selects an engine for the target URL using EngineMatcher rules."""

    name = "route"

    def __init__(self, matcher: object | None = None):
        self._matcher = matcher

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        matcher = self._matcher if self._matcher is not None else _DefaultEngineMatcher()
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
    """Assigns proxy, User-Agent, and TLS profile to evade detection."""

    name = "evade"

    def __init__(
        self,
        router: TLSRouter | None = None,
        proxies: list[str] | None = None,
    ):
        self._router = router or TLSRouter()
        self._proxies = proxies or []
        self._proxy_idx = 0

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

        logger.info(
            f"[evade] trace={ctx.trace_id} profile={profile.name} "
            f"proxy={ctx.proxy or 'none'} ja4={profile.ja4_prefix}"
        )
        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.proxy = ""
        ctx.user_agent = ""
        ctx.tls_profile = ""
        ctx.ja4_fingerprint = ""
        ctx.browser_profile = {}


class ExtractStage:
    """Navigates to target URL and extracts content.

    Two modes:
    1. HTTP mode (fast): httpx direct request
    2. Browser mode (full): Playwright/CloakBrowser via engine pool
    """

    name = "extract"

    def __init__(self, engine_factory=None):
        self._engine_factory = engine_factory

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.target_url:
            raise ConfigurationError("No target URL")

        logger.info(
            f"[extract] trace={ctx.trace_id} engine={ctx.selected_engine or 'http'} "
            f"url={ctx.target_url}"
        )

        # Step 1: Try lightweight HTTP
        html = await self._try_http(ctx)
        if html and len(html) > 200:
            ctx.raw_html = html
            ctx.extraction_confidence = 0.7
            return ctx

        # Step 2: Fall back to browser
        html = await self._try_browser(ctx)
        if html:
            ctx.raw_html = html
            ctx.extraction_confidence = 0.5
            return ctx

        raise ExtractionError(detail=f"Failed to fetch {ctx.target_url}")

    async def _try_http(self, ctx: PipelineContext) -> str | None:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                headers={"User-Agent": ctx.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}) as c:
                r = await c.get(ctx.target_url)
                r.raise_for_status()
                return r.text
        except Exception as e:
            logger.warning(f"[extract] HTTP failed: {e}")
            return None

    async def _try_browser(self, ctx: PipelineContext) -> str | None:
        if not self._engine_factory or not ctx.selected_engine:
            return None
        try:
            async with self._engine_factory.acquire(ctx.selected_engine) as engine:
                page = await engine.navigate(ctx.target_url, proxy=ctx.proxy or None)
                html = page.content
                await page.close()
                return html
        except Exception as e:
            logger.warning(f"[extract] Browser failed: {e}")
            return None

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.raw_html = ""
        ctx.markdown = ""
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
