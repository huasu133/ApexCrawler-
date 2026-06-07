"""
Pipeline hook system — 9 trigger points for custom behavior.
Allows injecting code at every stage of the crawl pipeline.
Inspiration: Crawl4AI's hook system (async_crawler_strategy.py).
"""

import asyncio
from typing import Callable, Optional, Any

# Available hook types matching pipeline lifecycle
HOOK_TYPES = {
    "on_start": "Called before pipeline execution begins. Args: (ctx)",
    "on_stage_start": "Called before each stage executes. Args: (ctx, stage_name)",
    "on_stage_end": "Called after each stage completes. Args: (ctx, stage_name, result)",
    "on_extract": "Called after extraction. Args: (ctx, extracted_data)",
    "on_error": "Called when a stage errors. Args: (ctx, stage_name, error)",
    "on_before_goto": "Called before navigating to URL. Args: (ctx, url)",
    "on_after_goto": "Called after page loads. Args: (ctx, page)",
    "on_before_return": "Called before returning results. Args: (ctx)",
    "on_complete": "Called when pipeline finishes. Args: (ctx, success)",
}


class PipelineHooks:
    """
    9 hook trigger points:
    - on_start: pipeline starting
    - on_stage_start: each stage begins
    - on_stage_end: each stage completes
    - on_extract: data extracted
    - on_error: stage error
    - on_before_goto: before URL navigation
    - on_after_goto: after page load
    - on_before_return: before result return
    - on_complete: pipeline finished
    """

    def __init__(self):
        self._hooks = {name: None for name in HOOK_TYPES}

    def set(self, hook_type: str, hook: Callable):
        """Register a hook. Accepts both sync and async functions."""
        if hook_type not in self._hooks:
            raise ValueError(
                f"Invalid hook type: {hook_type}. Valid: {list(HOOK_TYPES.keys())}"
            )
        self._hooks[hook_type] = hook

    def get(self, hook_type: str) -> Optional[Callable]:
        """Get the registered hook for a given type, or None."""
        return self._hooks.get(hook_type)

    async def execute(self, hook_type: str, *args, **kwargs) -> Optional[Any]:
        """Execute a hook if registered. Auto-detects sync vs async."""
        hook = self._hooks.get(hook_type)
        if hook:
            if asyncio.iscoroutinefunction(hook):
                return await hook(*args, **kwargs)
            return hook(*args, **kwargs)
        return None

    def clear(self, hook_type: Optional[str] = None):
        """Clear specific hook or all hooks."""
        if hook_type:
            self._hooks[hook_type] = None
        else:
            for k in self._hooks:
                self._hooks[k] = None
