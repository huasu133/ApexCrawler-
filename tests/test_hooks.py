"""Tests for the pipeline hook system."""

import pytest
from apexcrawler.pipeline.hooks import PipelineHooks, HOOK_TYPES


class TestPipelineHooks:
    def test_hook_types_count(self):
        assert len(HOOK_TYPES) == 9

    def test_set_and_execute(self):
        hooks = PipelineHooks()
        calls = []

        def my_hook(ctx):
            calls.append("called")

        hooks.set("on_start", my_hook)
        import asyncio

        asyncio.run(hooks.execute("on_start", "ctx"))
        assert len(calls) == 1

    def test_invalid_hook_type(self):
        hooks = PipelineHooks()
        with pytest.raises(ValueError):
            hooks.set("invalid_hook", lambda: None)

    def test_unset_hook_returns_none(self):
        hooks = PipelineHooks()
        import asyncio

        result = asyncio.run(hooks.execute("on_complete", "ctx"))
        assert result is None

    def test_clear_specific_hook(self):
        hooks = PipelineHooks()
        hooks.set("on_start", lambda: None)
        hooks.clear("on_start")
        assert hooks._hooks["on_start"] is None

    def test_clear_all_hooks(self):
        hooks = PipelineHooks()
        hooks.set("on_start", lambda: None)
        hooks.set("on_complete", lambda: None)
        hooks.clear()
        assert all(v is None for v in hooks._hooks.values())

    def test_get_registered_hook(self):
        hooks = PipelineHooks()
        fn = lambda ctx: "hello"
        hooks.set("on_start", fn)
        assert hooks.get("on_start") is fn

    def test_get_unregistered_hook(self):
        hooks = PipelineHooks()
        assert hooks.get("on_start") is None

    def test_execute_sync_hook_returns_value(self):
        hooks = PipelineHooks()
        hooks.set("on_start", lambda ctx: "result")
        import asyncio

        result = asyncio.run(hooks.execute("on_start", "ctx"))
        assert result == "result"

    def test_hook_types_keys(self):
        expected = [
            "on_start",
            "on_stage_start",
            "on_stage_end",
            "on_extract",
            "on_error",
            "on_before_goto",
            "on_after_goto",
            "on_before_return",
            "on_complete",
        ]
        assert list(HOOK_TYPES.keys()) == expected
