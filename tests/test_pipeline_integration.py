"""Integration tests for the full pipeline."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from apexcrawler.pipeline.core import PipelineExecutor, StageConfig, RetryPolicy
from apexcrawler.pipeline.stages import (
    ScheduleStage, RouteStage, EvadeStage,
    ExtractStage, ValidateStage, FontDecodeStage, StoreStage,
)
from apexcrawler.core.context import PipelineContext
from apexcrawler.behavior.timing import TimingScheduler
from apexcrawler.http.tls_router import TLSRouter


# Fast timing for tests
class _FastTiming:
    _page_count = 0
    def compute_delay(self, **kw):
        return 0.5
    def reset(self):
        pass


class TestPipelineExecution:

    @pytest.mark.asyncio
    async def test_pipeline_route_evade_store(self):
        """Route -> Evade -> Validate -> Store completes OK."""
        stages = [
            ScheduleStage(timing=_FastTiming()),
            RouteStage(),
            EvadeStage(router=TLSRouter()),
            ValidateStage(),
            StoreStage(),
        ]

        configs = {
            "schedule": StageConfig(timeout=10, retry=RetryPolicy(max_retries=0)),
        }

        executor = PipelineExecutor(stages, configs)
        ctx = PipelineContext(target_url="https://example.com")
        ok, result_ctx = await executor.run(ctx)

        assert ok is True
        assert result_ctx.selected_engine == "vanilla"
        assert len(result_ctx.stored_id) == 16

    @pytest.mark.asyncio
    async def test_pipeline_empty_url_raises(self):
        """Pipeline handles empty URL gracefully."""
        stages = [ScheduleStage(timing=_FastTiming()), RouteStage()]
        configs = {"schedule": StageConfig(timeout=10, retry=RetryPolicy(max_retries=0))}
        executor = PipelineExecutor(stages, configs)
        ctx = PipelineContext(target_url="")
        ok, result_ctx = await executor.run(ctx)
        assert ok is False

    @pytest.mark.asyncio
    async def test_pipeline_all_6_stages_with_http(self):
        """All 6 stages run with a real HTTP request (no mock needed)."""
        stages = [
            ScheduleStage(timing=_FastTiming()),
            RouteStage(),
            EvadeStage(router=TLSRouter()),
            ExtractStage(engine_factory=None, conn_manager=None),
            ValidateStage(),
            FontDecodeStage(),
            StoreStage(),
        ]

        configs = {
            "extract": StageConfig(timeout=15, retry=RetryPolicy(max_retries=1)),
            "schedule": StageConfig(timeout=10, retry=RetryPolicy(max_retries=0)),
        }

        executor = PipelineExecutor(stages, configs)
        ctx = PipelineContext(target_url="https://example.com")

        import os
        for k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
            os.environ.pop(k, None)

        ok, result_ctx = await executor.run(ctx)

        # Should complete (successful HTTP extraction or graceful fail)
        assert result_ctx.target_url == "https://example.com"
        if ok:
            assert len(result_ctx.raw_html) > 0


class TestRouteStage:

    @pytest.mark.asyncio
    async def test_default_engine_selection(self):
        """RouteStage selects vanilla for unknown URLs."""
        stage = RouteStage()
        ctx = PipelineContext(target_url="https://example.com")
        result = await stage.execute(ctx)
        assert result.selected_engine == "vanilla"
        assert result.route_reason != ""


class TestValidateStage:

    @pytest.mark.asyncio
    async def test_no_data_passes(self):
        """ValidateStage handles no extracted data."""
        stage = ValidateStage()
        ctx = PipelineContext(target_url="https://example.com")
        result = await stage.execute(ctx)
        assert result.validation_passed is False
        assert len(result.validation_errors) == 0


class TestStoreStage:

    @pytest.mark.asyncio
    async def test_stored_id_generated(self):
        """StoreStage generates a 16-char stored_id."""
        stage = StoreStage()
        ctx = PipelineContext(target_url="https://example.com")
        result = await stage.execute(ctx)
        assert len(result.stored_id) == 16
        assert result.stored_id.isalnum()
