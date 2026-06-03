"""Integration tests for ApexCrawler pipeline."""

import pytest
from apexcrawler.core.context import PipelineContext
from apexcrawler.pipeline.stages import (
    ScheduleStage,
    RouteStage,
    EvadeStage,
    ExtractStage,
    ValidateStage,
    StoreStage,
)
from apexcrawler.pipeline.core import PipelineExecutor, StageConfig


def test_pipeline_context_creation():
    ctx = PipelineContext(target_url="https://example.com")
    assert ctx.target_url == "https://example.com"
    assert len(ctx.trace_id) == 12
    assert ctx.retry_count == 0


@pytest.mark.asyncio
async def test_schedule_stage():
    ctx = PipelineContext(target_url="https://example.com")
    stage = ScheduleStage()
    result = await stage.execute(ctx)
    assert result.target_url == "https://example.com"


@pytest.mark.asyncio
async def test_route_stage():
    ctx = PipelineContext(target_url="https://example.com")
    stage = RouteStage()
    result = await stage.execute(ctx)
    assert result.selected_engine != ""


@pytest.mark.asyncio
async def test_full_pipeline():
    ctx = PipelineContext(target_url="https://httpbin.org/html")
    stages = [
        ScheduleStage(),
        RouteStage(),
        EvadeStage(),
        ExtractStage(),
        ValidateStage(),
        StoreStage(),
    ]
    configs = {"extract": StageConfig(timeout=20)}
    ok, result = await PipelineExecutor(stages, configs).run(ctx)
    assert ok
    assert len(result.raw_html) > 200
    assert result.stored_id != ""


@pytest.mark.asyncio
async def test_validate_stage():
    ctx = PipelineContext(target_url="https://example.com")
    ctx.raw_html = "<html></html>"
    stage = ValidateStage()
    result = await stage.execute(ctx)
    assert result.validation_passed is False or result.validation_errors is not None


def test_degrade_manager():
    from apexcrawler.pipeline.degrade import DegradeManager

    dm = DegradeManager()
    assert not dm.should_use_browser(
        PipelineContext(target_url="https://safe.com")
    )
    ctx = PipelineContext(target_url="https://blocked.com")
    ctx.raw_html = "captcha required"
    assert dm.should_use_browser(ctx)


def test_rate_controller():
    from apexcrawler.pipeline.rate_controller import RateController

    rc = RateController()
    assert rc.current_rate == 5
    rc.signal(status=429)
    assert rc.current_rate < 5


def test_session_manager():
    from apexcrawler.pipeline.session_manager import SessionManager

    sm = SessionManager()
    sm.bind_engine("test.com", "vanilla", "proxy1")
    assert sm.ensure_consistency("test.com", "vanilla")
    assert not sm.ensure_consistency("test.com", "cloaked")
