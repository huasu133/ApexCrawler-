"""ApexCrawler plugin system — hook into pipeline lifecycle events.

Plugins are callable objects or objects with hook methods that
register for specific pipeline events. They receive the PipelineContext
and can read/modify it.

Built-in hook points:
    on_pre_schedule(ctx)   — before ScheduleStage
    on_post_extract(ctx)   — after ExtractStage (raw_html available)
    on_pre_store(ctx)      — before StoreStage
    on_error(ctx, exc)     — when any stage raises

Usage:
    from apexcrawler.plugins import Plugin, PluginManager

    class LoggingPlugin(Plugin):
        def on_post_extract(self, ctx):
            print(f"Extracted {len(ctx.raw_html)} bytes from {ctx.target_url}")

    mgr = PluginManager()
    mgr.register(LoggingPlugin())
    # Pass mgr to PipelineExecutor
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger(__name__)


class Plugin(ABC):
    """Base class for ApexCrawler plugins.

    Override any hook method you need. All hooks are optional
    and receive the PipelineContext.
    """

    name: str = ""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if not cls.name:
            cls.name = cls.__name__

    async def on_pre_schedule(self, ctx: Any) -> None:
        """Called before ScheduleStage. Modify ctx.target_url, etc."""

    async def on_post_extract(self, ctx: Any) -> None:
        """Called after ExtractStage. Access ctx.raw_html, ctx.extracted_data."""

    async def on_pre_store(self, ctx: Any) -> None:
        """Called before StoreStage. Last chance to modify extracted data."""

    async def on_error(self, ctx: Any, exc: Exception) -> None:
        """Called when any stage raises. Log, alert, or retry."""


class PluginManager:
    """Manages plugin registration and hook dispatch.

    Registered plugins are called in registration order for each hook.
    Exceptions in plugins are caught and logged — they never break the pipeline.
    """

    def __init__(self):
        self._plugins: list[Plugin] = []

    def register(self, plugin: Plugin) -> None:
        """Register a plugin instance."""
        self._plugins.append(plugin)
        logger.info(f"Plugin registered: {plugin.name}")

    def unregister(self, plugin: Plugin) -> None:
        """Remove a previously registered plugin."""
        if plugin in self._plugins:
            self._plugins.remove(plugin)
            logger.info(f"Plugin unregistered: {plugin.name}")

    @property
    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    async def dispatch(self, hook: str, *args: Any) -> None:
        """Dispatch a hook to all registered plugins.

        Args:
            hook: Hook name (e.g. 'on_post_extract').
            *args: Arguments passed to each plugin's hook method.
        """
        for plugin in self._plugins:
            try:
                method = getattr(plugin, hook, None)
                if method is not None:
                    await method(*args)
            except Exception as e:
                logger.error(f"Plugin '{plugin.name}' hook '{hook}' failed: {e}")
