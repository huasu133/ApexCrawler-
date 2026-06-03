"""Engine pool — manages a pool of browser engine instances.

Provides async context manager for acquiring/releasing engine instances
with concurrency limits and health checking.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from apexcrawler.core.exceptions import EngineError, EnginePoolExhausted
from apexcrawler.engines.base import BaseEngine
from apexcrawler.routing.registry import EngineRegistry


class EnginePool:
    """Manages a pool of browser engine instances.

    Supports limiting concurrent engine usage per engine type and per pool.
    Uses asyncio.Semaphore for concurrency control.

    Usage:
        async with EnginePool(settings) as pool:
            async with pool.acquire("vanilla") as engine:
                page = await engine.navigate("https://example.com")
    """

    def __init__(
        self,
        engine_configs: dict[str, object] | None = None,
        max_total: int = 10,
    ) -> None:
        """Initialize the engine pool.

        Args:
            engine_configs: Dict mapping engine name to its config object.
            max_total: Maximum total concurrent engine instances across all types.
        """
        self._engine_configs = engine_configs or {}
        self._max_total = max_total
        self._total_semaphore = asyncio.Semaphore(max_total)
        self._engine_semaphores: dict[str, asyncio.Semaphore] = {}
        self._instantiated: dict[str, list[BaseEngine]] = {}

        # Build per-engine semaphores from config
        for name, cfg in self._engine_configs.items():
            max_conc = getattr(cfg, "max_concurrent", 1)
            self._engine_semaphores[name] = asyncio.Semaphore(max_conc)

    @asynccontextmanager
    async def acquire(self, engine_name: str) -> AsyncIterator[BaseEngine]:
        """Acquire an engine instance from the pool.

        Uses both a global semaphore and a per-engine semaphore for
        two-level concurrency control.

        Args:
            engine_name: Name of the registered engine to acquire.

        Yields:
            A launched engine instance ready for navigation.

        Raises:
            EnginePoolExhausted: If the pool is at maximum capacity.
        """
        engine_cls = EngineRegistry.get(engine_name)
        if engine_cls is None:
            raise EngineError(engine_name, f"Engine '{engine_name}' is not registered")

        engine_sem = self._engine_semaphores.get(engine_name)
        if engine_sem is None:
            engine_sem = asyncio.Semaphore(1)
            self._engine_semaphores[engine_name] = engine_sem

        # Acquire both semaphores with timeout to avoid deadlocks
        total_acquired = False
        try:
            await asyncio.wait_for(self._total_semaphore.acquire(), timeout=30)
            total_acquired = True
            await asyncio.wait_for(engine_sem.acquire(), timeout=30)
        except asyncio.TimeoutError:
            if total_acquired:
                self._total_semaphore.release()
            raise EnginePoolExhausted(self._max_total)

        engine_instance: BaseEngine | None = None
        try:
            cfg = self._engine_configs.get(engine_name)
            headless = getattr(cfg, "headless", True) if cfg else True
            viewport = getattr(cfg, "viewport", None) if cfg else None

            engine_instance = engine_cls(headless=headless, viewport=viewport)
            await engine_instance.launch()
            if engine_name not in self._instantiated:
                self._instantiated[engine_name] = []
            self._instantiated[engine_name].append(engine_instance)
            yield engine_instance
        finally:
            if engine_instance is not None:
                await engine_instance.close()
            engine_sem.release()
            self._total_semaphore.release()

    async def close_all(self) -> None:
        """Close all managed engine instances and reset the pool."""
        for engines in self._instantiated.values():
            for eng in engines:
                try:
                    await eng.close()
                except Exception:
                    pass
        self._instantiated.clear()

    async def __aenter__(self) -> EnginePool:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close_all()
