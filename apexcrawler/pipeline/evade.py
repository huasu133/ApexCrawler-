"""EvadeStage — Cloudflare-aware evasion and engine selection.

This module provides the EvadeStage which assigns proxy, User-Agent, TLS
profile, and device fingerprint to evade detection. It integrates the
CloudflareDetector to automatically detect Cloudflare-protected targets and
upgrade the browser engine to "cloaked" (the strongest anti-Cloudflare engine).

Usage:
    from apexcrawler.pipeline.evade import EvadeStage
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import TYPE_CHECKING

from ..core.exceptions import ConfigurationError

if TYPE_CHECKING:
    from ..core.context import PipelineContext
    from ..http.tls_router import TLSRouter
    from apexcrawler.fingerprint.consistency import DeviceProfile

logger = logging.getLogger(__name__)


# Engines that lack adequate Cloudflare bypass capability
_WEAK_CF_ENGINES = {"vanilla", "patched", "playwright"}

# Recommended engine for Cloudflare-protected targets
_CLOUDFLARE_ENGINE = "cloaked"


class EvadeStage:
    """Assigns proxy, User-Agent, TLS profile, and device fingerprint to evade detection.

    When a Cloudflare-protected target is detected, this stage will:
    - Prefer the "cloaked" engine (strongest anti-CF engine)
    - Upgrade "vanilla" or "patched" engines to "cloaked" if CF is suspected
    - Log detection events for observability
    """

    name = "evade"

    def __init__(
        self,
        router: "TLSRouter | None" = None,
        proxies: "list[str] | None" = None,
        device_profile: "DeviceProfile | None" = None,
    ):
        from ..http.tls_router import TLSRouter

        self._router = router or TLSRouter()
        self._proxies = proxies or []
        self._proxy_idx = 0
        self._proxy_lock = asyncio.Lock()
        if device_profile is None:
            from ..fingerprint.consistency import DEVICE_PROFILES
            self._device_profile = DEVICE_PROFILES[0]
        else:
            self._device_profile = device_profile

    async def execute(self, ctx: "PipelineContext") -> "PipelineContext":
        # ── Cloudflare detection and engine override ──
        try:
            from apexcrawler.decision.detectors import CloudflareDetector

            cf_detector = CloudflareDetector()
            html = ctx.raw_html or ""
            status = getattr(ctx, "_last_status", 200)
            cf_result = cf_detector.detect(html, {}, status, ctx.target_url)

            if cf_result.detected:
                # Cloudflare detected — check if current engine is strong enough
                if ctx.selected_engine in _WEAK_CF_ENGINES:
                    old_engine = ctx.selected_engine
                    ctx.selected_engine = CloudflareDetector.recommended_engine()
                    ctx.route_reason = (
                        f"Cloudflare detected (confidence={cf_result.confidence:.2f}): "
                        f"upgraded {old_engine} → {ctx.selected_engine}"
                    )
                    ctx.target_difficulty = max(ctx.target_difficulty, 8)
                    logger.warning(
                        f"[evade] trace={getattr(ctx, 'trace_id', '')} "
                        f"CF detected (cf={cf_result.confidence:.2f}, "
                        f"evidence={len(cf_result.evidence)}), "
                        f"engine: {old_engine} → {ctx.selected_engine}"
                    )
                else:
                    logger.info(
                        f"[evade] trace={getattr(ctx, 'trace_id', '')} "
                        f"CF detected but engine={ctx.selected_engine} is already adequate"
                    )
        except ImportError:
            logger.debug("CloudflareDetector not available, skipping CF detection")
        except Exception as e:
            logger.warning("Cloudflare detection failed: %s", e)

            # ── TLS profile assignment ──
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

        # ── Proxy assignment ──
        if self._proxies:
            async with self._proxy_lock:
                ctx.proxy = self._proxies[self._proxy_idx % len(self._proxies)]
                self._proxy_idx += 1

        # ── Device fingerprint attributes ──
        dp = self._device_profile
        ctx.webgl_renderer = dp.webgl_renderer
        ctx.canvas_hash = hashlib.sha256(
            f"{dp.name}:{dp.webgl_renderer}".encode()
        ).hexdigest()[:16]
        ctx.audio_fingerprint = hashlib.sha256(
            f"audio:{dp.name}:{dp.platform}".encode()
        ).hexdigest()[:16]
        ctx.fonts = [
            "Arial",
            "Times New Roman",
            "Courier New",
            "Georgia",
            "Verdana",
            "Trebuchet MS",
            "Comic Sans MS",
        ]

        logger.info(
            f"[evade] trace={getattr(ctx, 'trace_id', '')} "
            f"profile={profile.name} proxy={ctx.proxy or 'none'} "
            f"ja4={profile.ja4_prefix} device={dp.name} engine={ctx.selected_engine}"
        )
        return ctx

    async def rollback(self, ctx: "PipelineContext") -> None:
        ctx.proxy = ""
        ctx.user_agent = ""
        ctx.tls_profile = ""
        ctx.ja4_fingerprint = ""
        ctx.browser_profile = {}
        ctx.webgl_renderer = ""
        ctx.canvas_hash = ""
        ctx.audio_fingerprint = ""
        ctx.fonts = []
