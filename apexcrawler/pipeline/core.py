from __future__ import annotations
"""Async pipeline executor with retry, timeout, and rollback."""

import asyncio
import random
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from ..core.exceptions import NonRetryableError, RetryableError
from ..core.context import PipelineContext
from .checkpoint import CheckpointManager, _context_to_dict, _dict_to_context
from .hooks import PipelineHooks
from .rate_controller import DomainRateController

logger = logging.getLogger(__name__)
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
                 settings=None, session_manager=None, rate_controller=None, degrade_manager=None,
                 plugin_manager=None, checkpoint_dir: str | None = None,
                 hooks: PipelineHooks | None = None,
                 domain_rate_controller: DomainRateController | None = None):
        self._stages = stages
        self._configs = configs or {}
        self._session_mgr = session_manager
        self._rate_ctrl = rate_controller
        self._degrade_mgr = degrade_manager
        self._plugin_mgr = plugin_manager
        # Domain-level rate controller alongside the global rate controller
        self._domain_rate_ctrl = domain_rate_controller or DomainRateController()
        # Checkpoint manager
        self._checkpoint_mgr = CheckpointManager(
            storage_dir=checkpoint_dir or ".apex_checkpoints"
        ) if checkpoint_dir else None
        self._hooks = hooks
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
        for inter-stage pacing, DegradeManager for engine degradation,
        and PluginManager for lifecycle hooks.
        """
        executed = []

        # Plugin hook: on_pre_schedule
        if self._plugin_mgr:
            await self._plugin_mgr.dispatch("on_pre_schedule", ctx)

        # SessionManager: bind engine for domain consistency
        if self._session_mgr and ctx.selected_engine:
            from urllib.parse import urlparse
            domain = urlparse(ctx.target_url).netloc
            self._session_mgr.bind_engine(domain, ctx.selected_engine, ctx.proxy or "")

        # Hook: on_start — pipeline execution begins
        await self._safe_execute_hook("on_start", ctx)

        for stage in self._stages:
            # RateController: apply inter-stage pacing
            if self._rate_ctrl and stage.name in ("extract", "evade"):
                delay = await self._rate_ctrl.get_delay()
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

            # DomainRateController: domain-level rate limiting before extract
            if self._domain_rate_ctrl and stage.name == "extract":
                delay = await self._domain_rate_ctrl.get_delay(ctx.target_url)
                if delay > 0:
                    logger.debug(
                        f"[domain_rate] waiting {delay:.2f}s for "
                        f"{__import__('urllib.parse').urlparse(ctx.target_url).netloc}"
                    )
                    await asyncio.sleep(delay)

            # Hook: on_stage_start — before each stage executes
            await self._safe_execute_hook("on_stage_start", ctx, stage.name)
            # Hook: on_before_goto — before navigation (contextual, fires if stage is navigation-related)
            await self._safe_execute_hook("on_before_goto", ctx, ctx.target_url)

            cfg = self._configs.get(stage.name, StageConfig())
            try:
                ctx = await self._execute_with_retry(stage, ctx, cfg)
                executed.append(stage)

                # Save checkpoint after each successful stage
                if self._checkpoint_mgr:
                    ctx_dict = _context_to_dict(ctx)
                    self._checkpoint_mgr.save(ctx.trace_id, stage.name, ctx_dict)
                    logger.debug(
                        f"[checkpoint] saved after stage={stage.name} "
                        f"trace={ctx.trace_id}"
                    )

                # Plugin hook: on_post_extract
                if self._plugin_mgr and stage.name == "extract":
                    await self._plugin_mgr.dispatch("on_post_extract", ctx)

                # Plugin hook: on_pre_store
                if self._plugin_mgr and stage.name == "validate":
                    await self._plugin_mgr.dispatch("on_pre_store", ctx)

                # RateController: feed signal based on stage result
                if self._rate_ctrl:
                    if hasattr(ctx, 'raw_html') and ctx.raw_html and len(ctx.raw_html) > 0:
                        self._rate_ctrl.signal_success()

                # DomainRateController: record domain-level result
                if self._domain_rate_ctrl and stage.name == "extract":
                    status = getattr(ctx, '_last_status', 200)
                    html_len = len(ctx.raw_html or '')
                    await self._domain_rate_ctrl.record_result(ctx.target_url, status, html_len)

                # Hook: on_stage_end — stage completed successfully
                await self._safe_execute_hook("on_stage_end", ctx, stage.name, True)
                # Hook: on_after_goto — after page load (contextual)
                await self._safe_execute_hook("on_after_goto", ctx, ctx)
                # Hook: on_extract — after extraction completes
                if stage.name == "extract":
                    await self._safe_execute_hook("on_extract", ctx, ctx)

            except asyncio.CancelledError:
                await self._rollback(executed, ctx)
                raise
            except NonRetryableError as e:
                if self._plugin_mgr:
                    await self._plugin_mgr.dispatch("on_error", ctx, e)
                # Hook: on_error — non-retryable error
                await self._safe_execute_hook("on_error", ctx, stage.name, e)
                ctx.fatal_error = str(e)
                await self._rollback(executed, ctx)
                # Hook: on_before_return — before returning results on error
                await self._safe_execute_hook("on_before_return", ctx)
                # Hook: on_complete — pipeline finished with failure
                await self._safe_execute_hook("on_complete", ctx, False)
                return False, ctx
            except RetryableError as e:
                if self._plugin_mgr:
                    await self._plugin_mgr.dispatch("on_error", ctx, e)
                # Hook: on_error — retryable error
                await self._safe_execute_hook("on_error", ctx, stage.name, e)
                ctx.stage_errors.setdefault(stage.name, []).append(str(e))
                if self._rate_ctrl and stage.name == "extract":
                    self._rate_ctrl.signal(status=429)
                if self._domain_rate_ctrl and stage.name == "extract":
                    await self._domain_rate_ctrl.record_result(ctx.target_url, 429, 0)
                await self._rollback(executed, ctx)
                # Hook: on_before_return — before returning results on error
                await self._safe_execute_hook("on_before_return", ctx)
                # Hook: on_complete — pipeline finished with failure
                await self._safe_execute_hook("on_complete", ctx, False)
                return False, ctx
        # Hook: on_before_return — pipeline completed successfully
        await self._safe_execute_hook("on_before_return", ctx)
        # Hook: on_complete — pipeline finished with success
        await self._safe_execute_hook("on_complete", ctx, True)
        return True, ctx

    async def resume(self, job_id: str, ctx: PipelineContext | None = None):
        """从检查点恢复 pipeline 执行。

        加载指定 job_id 的最新检查点，跳过已完成的 stage，
        继续执行剩余的 pipeline。

        Args:
            job_id: 格式为 "{trace_id}_{stage}" 的作业 ID。
            ctx: 可选，提供初始上下文。若不提供，从检查点恢复。

        Returns:
            (success, ctx) 元组。
        """
        if not self._checkpoint_mgr:
            raise RuntimeError(
                "CheckpointManager is not initialized. "
                "Pass checkpoint_dir to PipelineExecutor to enable checkpointing."
            )

        checkpoint = self._checkpoint_mgr.load(job_id)
        if checkpoint is None:
            raise FileNotFoundError(f"Checkpoint not found for job_id={job_id}")

        data = checkpoint["context"]
        last_stage = checkpoint["stage"]

        # 恢复上下文
        from ..core.context import PipelineContext as PC
        restored_ctx = _dict_to_context(data, PC) if ctx is None else ctx
        logger.info(
            f"[checkpoint] resuming trace={restored_ctx.trace_id} "
            f"from stage={last_stage}"
        )

        # 找到已完成的 stage 索引，跳过它及之前的 stage
        skip_until = -1
        for i, stage in enumerate(self._stages):
            if stage.name == last_stage:
                skip_until = i
                break

        if skip_until < 0:
            logger.warning(
                f"[checkpoint] stage={last_stage} not found in current pipeline, "
                f"starting from beginning"
            )
            return await self.run(restored_ctx)

        remaining = self._stages[skip_until + 1:]
        if not remaining:
            logger.info(
                f"[checkpoint] all stages already completed for trace={restored_ctx.trace_id}"
            )
            return True, restored_ctx

        logger.info(
            f"[checkpoint] skipping {skip_until + 1} stage(s), "
            f"remaining: {[s.name for s in remaining]}"
        )

        # 只用剩余的 stage 创建临时 executor 并运行
        rescue_executor = PipelineExecutor(
            remaining,
            configs=self._configs,
            settings=None,
            session_manager=self._session_mgr,
            rate_controller=self._rate_ctrl,
            degrade_manager=self._degrade_mgr,
            plugin_manager=self._plugin_mgr,
            hooks=self._hooks,
            domain_rate_controller=self._domain_rate_ctrl,
        )
        return await rescue_executor.run(restored_ctx)

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

    async def _safe_execute_hook(self, hook_type: str, *args, **kwargs):
        """Execute a hook safely — hook failures never block the pipeline."""
        if not self._hooks:
            return
        try:
            await self._hooks.execute(hook_type, *args, **kwargs)
        except Exception:
            logger.warning(f"Hook '{hook_type}' failed, skipping.", exc_info=True)
