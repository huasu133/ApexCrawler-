"""Concrete pipeline stage implementations.

Stages in order: Schedule → Route → Evade → Extract → FontDecode → Validate → Store
Each stage reads from and writes to PipelineContext.
"""

from __future__ import annotations

import asyncio
import hashlib
import httpx
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from ..core.context import PipelineContext
from ..core.exceptions import ConfigurationError, ExtractionError
from ..decision.engine import DecisionEngine
from ..http.tls_router import TLSRouter

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = os.path.expanduser("~/.apexcrawler/page_cache")


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
            logger.debug(
                f"[schedule] trace={ctx.trace_id} computed_delay={delay:.2f}s"
                f" url={ctx.target_url}"
            )
            await asyncio.sleep(delay)
        else:
            logger.debug("[schedule] trace=%s url=%s", ctx.trace_id, ctx.target_url)

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
        # If an engine was already forced (e.g. via CLI --engine or MCP param),
        # skip routing and preserve the user's explicit choice
        if ctx.selected_engine:
            logger.debug(
                f"[route] trace={ctx.trace_id} engine already forced: "
                f"{ctx.selected_engine} (skipping routing)"
            )
            return ctx

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

        logger.debug(
            f"[route] trace={ctx.trace_id} engine={ctx.selected_engine} "
            f"reason={ctx.route_reason} difficulty={ctx.target_difficulty}"
        )
        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.selected_engine = ""
        ctx.route_reason = ""
        ctx.target_difficulty = 0


class EvadeStage:
    """Assigns proxy, User-Agent, TLS profile, and device fingerprint to evade detection.

    When a Cloudflare-protected target is detected, this stage will:
    - Prefer the "camoufox" engine (Firefox-based, better CF compatibility)
    - Upgrade "vanilla" or "patched" engines to "camoufox" if CF is suspected
    - Set engine preference metadata for downstream stages
    """

    name = "evade"

    # Engines that lack adequate Cloudflare bypass capability
    _WEAK_CF_ENGINES = {"vanilla", "patched", "playwright"}

    # Recommended engine for Cloudflare-protected targets
    _CLOUDFLARE_ENGINE = "camoufox"

    def __init__(
        self,
        router: TLSRouter | None = None,
        proxies: list[str] | None = None,
        device_profile: "DeviceProfile | None" = None,
    ):
        self._router = router or TLSRouter()
        self._proxies = proxies or []
        self._proxy_idx = 0
        self._proxy_lock = asyncio.Lock()
        if device_profile is None:
            from ..fingerprint.consistency import DEVICE_PROFILES
            self._device_profile = DEVICE_PROFILES[0]
        else:
            self._device_profile = device_profile

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        # Check for Cloudflare signal from route stage or previous stage results
        cf_detected = self._check_cloudflare_signal(ctx)

        # If Cloudflare is suspected and current engine is weak, upgrade it
        if cf_detected and ctx.selected_engine in self._WEAK_CF_ENGINES:
            old_engine = ctx.selected_engine
            ctx.selected_engine = self._CLOUDFLARE_ENGINE
            ctx.route_reason = (
                f"Cloudflare detected: upgraded {old_engine} → {self._CLOUDFLARE_ENGINE}"
            )
            ctx.target_difficulty = max(ctx.target_difficulty, 8)
            logger.warning(
                f"[evade] trace={ctx.trace_id} CF detection triggered engine upgrade: "
                f"{old_engine} → {self._CLOUDFLARE_ENGINE}"
            )

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
            async with self._proxy_lock:
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

        logger.debug(
            f"[evade] trace={ctx.trace_id} profile={profile.name} "
            f"proxy={ctx.proxy or 'none'} ja4={profile.ja4_prefix} "
            f"device={dp.name} engine={ctx.selected_engine}"
        )
        return ctx

    def _check_cloudflare_signal(self, ctx: PipelineContext) -> bool:
        """Check if Cloudflare is suspected based on context signals.

        Examines route reason, target difficulty, and any detection results
        already stored in the context.
        """
        # Check route reason for cloudflare keyword
        if "cloudflare" in ctx.route_reason.lower():
            return True

        # Check if difficulty is high (CF targets are usually high)
        if ctx.target_difficulty >= 9:
            return True

        # If URL contains cloudflare-related keywords
        cf_keywords = ["cloudflare", "cf-", "challenge"]
        target_url = ctx.target_url.lower() if ctx.target_url else ""
        if any(kw in target_url for kw in cf_keywords):
            return True

        return False

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

        logger.debug(
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
                logger.debug("[extract] cleaner applied, %s bytes", len(ctx.raw_html))
            except Exception:
                pass

            # Step 1b: Enhance with Crawl4AI content filtering
            # Run when HTTP content is available but confidence is moderate
            try:
                await self._try_crawl4ai(ctx)
            except Exception as e:
                logger.debug("[extract] crawl4ai enhancement skipped: %s", e)

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
                logger.debug("[extract] sel_healer recovered content")
                return ctx
        except Exception as e:
            logger.debug("[extract] sel_healer failed: %s", e)

        raise ExtractionError(detail=f"Failed to fetch {ctx.target_url}")

    async def _try_http(self, ctx: PipelineContext) -> str | None:
        # Step 0: Probe mobile/API endpoints before hitting the full page
        mobile_endpoint = await self._mobile_sniffer.probe(ctx.target_url)
        fetch_url = mobile_endpoint.url if mobile_endpoint else ctx.target_url
        is_api = mobile_endpoint is not None and mobile_endpoint.confidence >= 0.9

        if mobile_endpoint:
            logger.debug(
                f"[extract] mobile_sniffer → {fetch_url} "
                f"(confidence={mobile_endpoint.confidence:.1f}, source={mobile_endpoint.source})"
            )

        try:
            from urllib.parse import urlparse, urlunparse
            from ..utils.dns_cache import dns_cache

            target_url = fetch_url
            headers = {"User-Agent": ctx.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}

            # DNS cache: resolve host to IP for faster connection (HTTP only; HTTPS needs hostname for SSL)
            parsed = urlparse(target_url)
            host = parsed.netloc.split(":")[0]
            if parsed.scheme == "http":
                resolved_ip = dns_cache.resolve(host)
                if resolved_ip != host:
                    netloc = parsed.netloc.replace(host, resolved_ip)
                    target_url = urlunparse(parsed._replace(netloc=netloc))
                    headers["Host"] = host

            # Connection reuse: obtain proxy URL from ConnectionReuseManager
            proxy = None
            if self._conn_manager is not None:
                proxy = await self._conn_manager.get_proxy(ctx.target_url)

            async with httpx.AsyncClient(
                timeout=15, follow_redirects=True,
                headers=headers,
            ) as c:
                r = await c.get(target_url)
                ctx._last_status = r.status_code
                ctx._last_headers = dict(r.headers)
                r.raise_for_status()
                html = r.text

            # Brotli decompression support
            if r.headers.get("Content-Encoding") == "br":
                from ..utils.brotli_support import decompress_brotli
                raw = r.content
                decompressed = decompress_brotli(raw)
                if decompressed:
                    html = decompressed.decode("utf-8", errors="replace")
                    logger.debug("[extract] brotli decompressed %s → %s bytes", len(raw), len(html))

            # If we got JSON from a mobile API, use it directly — skip browser fallback
            if is_api:
                self._api_detected = True
                ctx.extraction_confidence = 0.8
                logger.debug(
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

            # Structured data extraction (priority: JSON-LD > OG > Microdata > CSS)
            try:
                structured = self._extract_structured_data(html)
                if structured and len(structured) > 0:
                    # If structured data has meaningful content, elevate confidence
                    ctx.extracted_data = structured
                    ctx.extraction_confidence = max(ctx.extraction_confidence or 0.5, 0.85)
                    logger.debug(
                        f"[extract] structured data found: {len(structured)} fields "
                        f"(json_ld={len([k for k in structured if not k.startswith(('og_','md_'))])}, "
                        f"og={len([k for k in structured if k.startswith('og_')])}, "
                        f"microdata={len([k for k in structured if k.startswith('md_')])})"
                    )
            except Exception as e:
                logger.debug("[extract] structured data extraction skipped: %s", e)

            return html
        except Exception as e:
            logger.warning("[extract] HTTP failed: %s", e)
            return None

    async def _try_browser(self, ctx: PipelineContext) -> str | None:
        if not self._engine_factory or not ctx.selected_engine:
            return None
        try:
            async with self._engine_factory.acquire(ctx.selected_engine) as engine:
                page = await engine.navigate(ctx.target_url, proxy=ctx.proxy or None)
                html = await page.content()
                await page.close()
                return html
        except Exception as e:
            logger.warning("[extract] Browser failed: %s", e)
            return None

    async def _try_crawl4ai(self, ctx: PipelineContext) -> None:
        """Enhance extracted content using Crawl4AI's capabilities.

        Two modes:
        1. LLM Extraction — if ctx has llm_provider set, use
           LLMExtractionStrategy with schema/instruction.
        2. Content Filtering — default: use PruningContentFilter
           (or BM25ContentFilter if content_filter_query is set)
           to produce cleaner markdown.
        """
        if not ctx.raw_html or len(ctx.raw_html) < 500:
            return

        # ── Mode 1: LLM Extraction ──
        if ctx.llm_provider:
            try:
                from apexcrawler.extraction.llm_extract import (
                    extract_with_llm, LLMConfig,
                )
            except ImportError:
                logger.debug("[extract] llm_extract module not available")
                return

            llm_config = LLMConfig(
                provider=ctx.llm_provider,
                api_token=ctx.llm_api_token,
                instruction=ctx.llm_instruction,
                input_format="markdown",
            )
            if ctx.llm_schema_json:
                try:
                    import json
                    llm_config.schema = json.loads(ctx.llm_schema_json)
                except json.JSONDecodeError:
                    logger.warning("[extract] invalid llm schema: %s", ctx.llm_schema_json[:80])

            result = extract_with_llm(ctx.raw_html, llm_config)
            if result.get("success"):
                ctx.extracted_data = result["data"]
                ctx.extraction_confidence = 0.9  # LLM extraction is highly reliable
                if isinstance(result["data"], str):
                    ctx.raw_crawl4ai = result["data"]
                logger.info(
                    "[extract] LLM extraction succeeded, "
                    f"confidence={ctx.extraction_confidence}"
                )
            else:
                logger.warning("[extract] LLM extraction failed: %s", result.get('error'))
            return  # LLM extraction replaces content filtering

        # ── Mode 2: Content Filtering (default) ──
        if ctx.extraction_confidence >= 0.85:
            return

        try:
            from crawl4ai.content_filter_strategy import (
                PruningContentFilter, BM25ContentFilter,
            )
            from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        except ImportError:
            logger.debug("[extract] crawl4ai not available, skipping enhancement")
            return

        # Select filter type
        if ctx.content_filter_query:
            filter_ = BM25ContentFilter(
                user_query=ctx.content_filter_query,
                bm25_threshold=1.0,
            )
        else:
            filter_ = PruningContentFilter(threshold=0.48, threshold_type="fixed")

        md_generator = DefaultMarkdownGenerator(content_filter=filter_)
        result = md_generator.generate_markdown(ctx.raw_html)

        clean_md = ""
        if hasattr(result, "fit_markdown") and result.fit_markdown:
            clean_md = result.fit_markdown
        elif hasattr(result, "raw_markdown") and result.raw_markdown:
            clean_md = result.raw_markdown

        if clean_md and len(clean_md) > 50:
            ctx.raw_crawl4ai = clean_md
            original_len = len(ctx.raw_html)
            ratio = len(clean_md) / max(original_len, 1)
            logger.debug(
                f"[extract] crawl4ai: {original_len}B HTML → {len(clean_md)}B md "
                f"(ratio={ratio:.2f})"
            )
            if ratio < 0.8 and len(clean_md) > 100:
                ctx.extraction_confidence = max(ctx.extraction_confidence or 0, 0.75)

    def _extract_structured_data(self, html: str) -> dict | None:
        """Extract JSON-LD, Open Graph, and Microdata from HTML.

        Structured data (JSON-LD, OG, Microdata) is more stable than
        CSS selectors because it's embedded in the page metadata and
        doesn't change with layout updates.
        """
        result = {}

        # 1. JSON-LD (most reliable)
        try:
            import json, re
            ld_json_matches = re.findall(
                r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                html, re.DOTALL | re.IGNORECASE
            )
            for match in ld_json_matches:
                try:
                    data = json.loads(match.strip())
                    # Flatten @graph if present
                    if '@graph' in data:
                        for item in data['@graph']:
                            if isinstance(item, dict):
                                result.update(item)
                    else:
                        result.update(data)
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

        # 2. Open Graph meta tags
        try:
            import re
            og_matches = re.findall(
                r'<meta[^>]+property="(?:og|article|product):([^"]+)"[^>]+content="([^"]*)"',
                html, re.IGNORECASE
            )
            for key, value in og_matches:
                result[f"og_{key}"] = value
        except Exception:
            pass

        # 3. Microdata (itemprop)
        try:
            import re
            microdata_matches = re.findall(
                r'<[^>]+itemprop="([^"]+)"[^>]+content="([^"]*)"',
                html, re.IGNORECASE
            )
            for key, value in microdata_matches:
                result[f"md_{key}"] = value
        except Exception:
            pass

        return result if result else None

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.raw_html = ""
        ctx.extracted_data = None
        ctx.extraction_confidence = 0.0


class ValidateStage:
    """Validates extracted data against the target schema, then cleans it."""

    name = "validate"

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        ctx.validation_errors = []

        if ctx.extracted_data is None:
            logger.debug(
                f"[validate] trace={ctx.trace_id} no extracted data to validate "
                f"(confidence={ctx.extraction_confidence:.2f})"
            )
            ctx.validation_passed = False
            return ctx

        schema = ctx.extraction_schema
        if schema is None:
            logger.debug("[validate] trace=%s no schema — pass-through", ctx.trace_id)
            ctx.validation_passed = True
            # Clean data even without schema validation
            from ..extraction.cleaner import clean_record
            ctx.cleaned_data = clean_record(ctx.extracted_data)
            logger.debug("[validate] clean_record applied, cleaned_data keys=%s", list(ctx.cleaned_data.keys()))
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

        # Clean extracted data only if validation passed
        if ctx.validation_passed and ctx.extracted_data:
            from ..extraction.cleaner import clean_record
            ctx.cleaned_data = clean_record(ctx.extracted_data)
            logger.debug("[validate] clean_record applied, cleaned_data keys=%s", list(ctx.cleaned_data.keys()))

        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.validation_passed = False
        ctx.validation_errors = []


class StoreStage:
    """Persists the crawl result.

    For now, logs the result and writes a stored_id to the context.
    In production this would write to a database, object store, or queue.

    Supports incremental crawling via ETag/Last-Modified tracking,
    avoiding redundant downloads of unchanged pages.
    """

    name = "store"

    def __init__(self, cache_dir: str | None = None):
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
        except OSError as e:
            logger.warning("Failed to create cache dir %s: %s", self._cache_dir, e)

    def _get_cache_path(self, url: str) -> Path:
        """Get filesystem path for URL cache."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
        return Path(self._cache_dir) / f"{url_hash}.json"

    def _check_cached(self, url: str, headers: dict) -> str | None:
        """Check if page is unchanged via ETag/Last-Modified.

        Returns the cached HTML if the page hasn't changed, None otherwise.
        """
        cache_path = self._get_cache_path(url)
        if not cache_path.exists():
            return None

        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            etag = headers.get("ETag", headers.get("etag", ""))
            last_modified = headers.get("Last-Modified", headers.get("last-modified", ""))

            # If ETag matches, page hasn't changed
            if etag and cached.get("etag") == etag:
                logger.debug("[store] ETag match for %s, using cache", url)
                return cached.get("html", "")

            # If Last-Modified matches, page hasn't changed
            if last_modified and cached.get("last_modified") == last_modified:
                logger.debug("[store] Last-Modified match for %s, using cache", url)
                return cached.get("html", "")
        except (json.JSONDecodeError, OSError):
            pass

        return None

    def _save_to_cache(self, url: str, html: str, headers: dict):
        """Save page to cache with ETag/Last-Modified metadata."""
        cache_path = self._get_cache_path(url)
        content_hash = hashlib.sha256(html.encode()).hexdigest()

        cache_data = {
            "url": url,
            "html": html,
            "content_hash": content_hash,
            "etag": headers.get("ETag", headers.get("etag", "")),
            "last_modified": headers.get("Last-Modified", headers.get("last-modified", "")),
            "cached_at": __import__('time').time(),
        }

        try:
            cache_path.write_text(json.dumps(cache_data, ensure_ascii=False), encoding="utf-8")
            logger.debug("[store] cached %s (%s bytes)", url, len(html))
        except OSError as e:
            logger.warning("[store] failed to cache %s: %s", url, e)

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        """Enhanced execute with incremental crawling support."""
        # Generate stored_id
        key = f"{ctx.trace_id}:{ctx.target_url}"
        ctx.stored_id = hashlib.sha256(key.encode()).hexdigest()[:16]

        # Check for cache hit (incremental crawling)
        if ctx.raw_html and hasattr(ctx, '_last_headers'):
            cached_html = self._check_cached(ctx.target_url, ctx._last_headers)
            if cached_html:
                ctx.raw_html = cached_html
                ctx.incremental_hit = True
                logger.debug(
                    f"[store] incremental cache hit for {ctx.target_url} "
                    f"(stored_id={ctx.stored_id})"
                )
            else:
                # Save to cache for future incremental checks
                self._save_to_cache(ctx.target_url, ctx.raw_html, ctx._last_headers)
                ctx.incremental_hit = False

        logger.debug(
            f"[store] trace={ctx.trace_id} stored_id={ctx.stored_id} "
            f"engine={ctx.selected_engine} duration={ctx.duration():.2f}s "
            f"valid={ctx.validation_passed}"
        )
        return ctx

    async def rollback(self, ctx: PipelineContext) -> None:
        ctx.stored_id = ""


# ── Internal helpers ──────────────────────────────────────────────


class FontDecodeStage:
    """Decodes font-encoded text using FontCracker + OCREngine + DOMFixer.

    Runs after extraction to decode any font-obfuscated content (e.g. 58同城, 猫扑).
    Falls back through FontTools → OCR → shape matching.
    """

    name = "font_decode"

    def __init__(self, font_cracker=None, ocr_engine=None, dom_fixer=None):
        from ..anti_font.font_cracker import FontCracker as FC
        from ..anti_font.ocr_engine import OCREngine
        from ..anti_font.dom_fixer import DOMFixer
        self._cracker = font_cracker or FC()
        self._ocr = ocr_engine or OCREngine()
        self._fixer = dom_fixer or DOMFixer()

    async def execute(self, ctx: PipelineContext) -> PipelineContext:
        if not ctx.raw_html or "@font-face" not in ctx.raw_html:
            logger.debug("[font_decode] no encoded fonts detected, skipping")
            return ctx

        try:
            # Step 1: Fix DOM CSS position offsets
            fixed_html = self._fixer.fix(ctx.raw_html)
            ctx.raw_html = fixed_html
            logger.debug("[font_decode] DOMFixer applied")

            # Step 2: Try FontTools glyph mapping
            decoded = await self._cracker.decode(ctx.raw_html)
            if decoded and decoded != ctx.raw_html:
                ctx.raw_html = decoded
                ctx.extraction_confidence = max(ctx.extraction_confidence or 0, 0.85)
                logger.debug("[font_decode] FontTools decoded successfully")
                return ctx

            # Step 3: OCR fallback for dynamic glyphs
            ocr_result = await self._ocr.decode(ctx.raw_html)
            if ocr_result and ocr_result.confidence > 0.7:
                ctx.raw_html = ocr_result.text
                ctx.extraction_confidence = max(ctx.extraction_confidence or 0, ocr_result.confidence)
                logger.debug(
                    f"[font_decode] OCR decoded (confidence={ocr_result.confidence:.2f})"
                )
        except Exception as e:
            logger.warning("[font_decode] failed: %s", e)

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
        return _MatchResult(engine="vanilla", reason="default", difficulty=1)
