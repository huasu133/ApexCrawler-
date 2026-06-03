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

    def __init__(self, stages: list, configs: dict[str, StageConfig] | None = None, settings=None):
        self._stages = stages
        self._configs = configs or {}
        # Merge stage_timeouts from Settings if provided
        if settings:
            stage_timeouts = getattr(settings.pipeline, "stage_timeouts", {})
            for name, timeout in stage_timeouts.items():
                if name not in self._configs:
                    self._configs[name] = StageConfig(timeout=float(timeout))

    async def run(self, ctx: PipelineContext):
        """Run all stages sequentially. Returns (success, ctx)."""
        executed = []
        for stage in self._stages:
            cfg = self._configs.get(stage.name, StageConfig())
            try:
                ctx = await self._execute_with_retry(stage, ctx, cfg)
                executed.append(stage)
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
        raise last_exc

    async def _rollback(self, stages, ctx):
        for stage in reversed(stages):
            try:
                await asyncio.wait_for(stage.rollback(ctx), timeout=10.0)
            except Exception:
                logger.error(f"Rollback failed for {stage.name}")
