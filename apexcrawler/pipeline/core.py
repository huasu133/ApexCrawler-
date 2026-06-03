from __future__ import annotations
"""Async pipeline executor with retry, timeout, and rollback."""

import asyncio
import random
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TypeVar
from ..core.exceptions import NonRetryableError, RetryableError
from ..core.context import PipelineContext

logger = logging.getLogger(__name__)
T = TypeVar("T")


@dataclass
class RetryPolicy:
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    jitter: bool = True


@dataclass
class StageConfig:
    timeout: float = 30.0
    retry: RetryPolicy = field(default_factory=RetryPolicy)


class PipelineExecutor:
    """Async pipeline executor with retry, timeout, and rollback."""

    def __init__(self, stages: list, configs: dict[str, StageConfig] | None = None,
                 settings=None, session_manager=None, rate_controller=None, degrade_manager=None):
        self._stages = stages
        self._configs = configs or {}
        self._session_mgr = session_manager
        self._rate_ctrl = rate_controller
        self._degrade_mgr = degrade_manager
        # Merge stage_timeouts from Settings if provided
        if settings:
            pipeline_cfg = getattr(settings, 'pipeline', None)
            stage_timeouts = getattr(pipeline_cfg, 'stage_timeouts', {}) if pipeline_cfg else {}
            for name, timeout in stage_timeouts.items():
                if name not in self._configs:
                    self._configs[name] = StageConfig(timeout=float(timeout))

    async def run(self, ctx: PipelineContext):
        """Run all stages sequentially. Returns (success, ctx).

        Integrates SessionManager for session tracking, RateController
        for inter-stage pacing, and DegradeManager for engine degradation.
        """
        executed = []

        # SessionManager: bind engine for domain consistency
        if self._session_mgr and ctx.selected_engine:
            from urllib.parse import urlparse
            domain = urlparse(ctx.target_url).netloc
            self._session_mgr.bind_engine(domain, ctx.selected_engine, ctx.proxy or "")

        for stage in self._stages:
            # RateController: apply inter-stage pacing
            if self._rate_ctrl and stage.name in ("extract", "evade"):
                delay = self._rate_ctrl.get_delay()
                if delay > 0:
                    await asyncio.sleep(delay)

            # DegradeManager: check if engine degradation is needed
            if self._degrade_mgr and stage.name == "extract":
                if self._degrade_mgr.should_degrade(ctx):
                    old_engine = ctx.selected_engine
                    ctx.selected_engine = self._degrade_mgr.degrade(ctx.selected_engine)
                    logger.warning(
                        f"[degrade] engine degraded {old_engine} → {ctx.selected_engine}"
                    )

            cfg = self._configs.get(stage.name, StageConfig())
            try:
                ctx = await self._execute_with_retry(stage, ctx, cfg)
                executed.append(stage)

                # RateController: feed signal based on stage result
                if self._rate_ctrl:
                    if hasattr(ctx, 'raw_html') and ctx.raw_html and len(ctx.raw_html) > 200:
                        self._rate_ctrl.signal_success()
                    elif stage.name == "extract":
                        self._rate_ctrl.signal(status=429)

            except asyncio.CancelledError:
                await self._rollback(executed, ctx)
                raise
            except NonRetryableError as e:
                ctx.fatal_error = str(e)
                await self._rollback(executed, ctx)
                return False, ctx
            except RetryableError as e:
                ctx.stage_errors.setdefault(stage.name, []).append(str(e))
                await self._rollback(executed, ctx)
                return False, ctx
        return True, ctx

    async def _execute_with_retry(self, stage, ctx, cfg):
        last_exc = None
        for attempt in range(cfg.retry.max_retries + 1):
            try:
                return await asyncio.wait_for(stage.execute(ctx), timeout=cfg.timeout)
            except asyncio.TimeoutError as e:
                last_exc = RetryableError(f"Stage {stage.name} timed out after {cfg.timeout}s")
                if attempt < cfg.retry.max_retries:
                    delay = min(cfg.retry.base_delay * (2 ** attempt), cfg.retry.max_delay)
                    if cfg.retry.jitter:
                        delay *= 0.5 + random.random()
                    logger.warning(
                        f"Stage {stage.name} retry {attempt+1}/{cfg.retry.max_retries} in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
            except RetryableError as e:
                last_exc = e
                if attempt < cfg.retry.max_retries:
                    delay = min(cfg.retry.base_delay * (2 ** attempt), cfg.retry.max_delay)
                    if cfg.retry.jitter:
                        delay *= 0.5 + random.random()
                    logger.warning(
                        f"Stage {stage.name} retry {attempt+1}/{cfg.retry.max_retries} in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
        if last_exc is None:
            raise RuntimeError(
                f"Stage {stage.name}: no exception captured but retries exhausted"
            )
        raise last_exc

    async def _rollback(self, stages, ctx):
        for stage in reversed(stages):
            try:
                await asyncio.wait_for(stage.rollback(ctx), timeout=10.0)
            except Exception:
                logger.error(f"Rollback failed for {stage.name}")
