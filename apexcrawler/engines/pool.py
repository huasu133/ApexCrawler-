"""Engine pool — manages a pool of browser engine instances.

Provides async context manager for acquiring/releasing engine instances
with concurrency limits, health checking, and idle-engine reuse.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

from apexcrawler.core.exceptions import EngineError, EnginePoolExhausted
from apexcrawler.engines.base import BaseEngine
from apexcrawler.routing.registry import EngineRegistry


@dataclass
class HybridSession:
    """Result from hybrid crawl mode.

    After browser login/authentication, subsequent requests can reuse
    the browser's cookies via FastFetcher, saving ~97% proxy bandwidth.
    """
    engine: BaseEngine
    cookies: dict[str, str]
    user_agent: str

    async def get_fetcher(self) -> "FastFetcher":
        """Create a FastFetcher pre-populated with browser cookies."""
        from apexcrawler.http.fetcher import FastFetcher
        fetcher = FastFetcher(impersonate="chrome131")
        # Set cookies
        for name, value in self.cookies.items():
            fetcher.session.cookies.set(name, value)
        return fetcher


class EnginePool:
    """Manages a pool of browser engine instances.

    Supports limiting concurrent engine usage per engine type and per pool.
    Uses asyncio.Semaphore for concurrency control.
    Reuses idle engines across acquire() calls via health_check().

    Usage:
        async with EnginePool(settings) as pool:
            async with pool.acquire("vanilla") as engine:
                page = await engine.navigate("https://example.com")
    """

    def __init__(
        self,
        engine_configs: dict[str, object] | None = None,
        max_total: int = 10,
        max_idle_per_engine: int = 2,
    ) -> None:
        """Initialize the engine pool.

        Args:
            engine_configs: Dict mapping engine name to its config object.
            max_total: Maximum total concurrent engine instances across all types.
            max_idle_per_engine: Maximum idle engines to keep per type.
        """
        self._engine_configs = engine_configs or {}
        self._max_total = max_total
        self._max_idle_per_engine = max_idle_per_engine
        self._total_semaphore = asyncio.Semaphore(max_total)
        self._engine_semaphores: dict[str, asyncio.Semaphore] = {}
        # Idle engines available for reuse (not currently acquired)
        self._idle_engines: dict[str, list[BaseEngine]] = {}
        # Engines currently acquired and in use
        self._active_engines: set[BaseEngine] = set()

        # Build per-engine semaphores from config
        for name, cfg in self._engine_configs.items():
            max_conc = getattr(cfg, "max_concurrent", 1)
            self._engine_semaphores[name] = asyncio.Semaphore(max_conc)

    @asynccontextmanager
    async def acquire(self, engine_name: str) -> AsyncIterator[BaseEngine]:
        """Acquire an engine instance from the pool.

        Uses both a global semaphore and a per-engine semaphore for
        two-level concurrency control.  Reuses idle engines if a healthy
        one is available; otherwise creates a fresh instance.

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

        cfg = self._engine_configs.get(engine_name)
        headless = getattr(cfg, "headless", True) if cfg else True
        viewport = getattr(cfg, "viewport", None) if cfg else None
        extra_args = getattr(cfg, "extra_args", None) if cfg else None
        launch_timeout = getattr(cfg, "timeout_seconds", 30) if cfg else 30

        engine_instance: BaseEngine | None = None
        reused = False

        # ── Phase 1: try to reuse an idle engine ────────────────
        idle_list = self._idle_engines.get(engine_name, [])
        while idle_list:
            candidate = idle_list.pop()
            try:
                if await candidate.health_check():
                    engine_instance = candidate
                    reused = True
                    break
                else:
                    await candidate.close()
            except Exception:
                await candidate.close()

        # ── Phase 2: create a new engine if nothing to reuse ────
        if not reused:
            kwargs: dict = {"headless": headless, "viewport": viewport}
            if extra_args is not None:
                kwargs["extra_args"] = extra_args
            engine_instance = engine_cls(**kwargs)
            await asyncio.wait_for(engine_instance.launch(), timeout=launch_timeout)

        try:
            self._active_engines.add(engine_instance)
            yield engine_instance
        finally:
            self._active_engines.discard(engine_instance)
            # Return engine to the idle pool instead of closing it,
            # unless the idle pool has reached capacity.
            idle_pool = self._idle_engines.setdefault(engine_name, [])
            if len(idle_pool) >= self._max_idle_per_engine:
                await engine_instance.close()
            else:
                idle_pool.append(engine_instance)
            engine_sem.release()
            self._total_semaphore.release()

    @asynccontextmanager
    async def acquire_hybrid(self, engine_name: str) -> AsyncIterator[HybridSession]:
        """Acquire a browser engine with cookie extraction for hybrid crawling.

        After acquiring the engine and navigating to a URL, callers can
        extract the browser's cookies and reuse them via FastFetcher,
        saving proxy bandwidth on subsequent requests.

        Usage:
            async with pool.acquire_hybrid("cloaked") as session:
                page = await session.engine.navigate("https://example.com/login")
                # ... perform login interaction ...
                # Extract cookies after login
                cookies = await page.evaluate("document.cookie")
                session.cookies = _parse_cookies(cookies)
                # Now use session.get_fetcher() for follow-up requests

        Args:
            engine_name: Name of the registered engine to acquire.

        Yields:
            A HybridSession containing the engine, cookies, and user agent.
        """
        engine_cls = EngineRegistry.get(engine_name)
        if engine_cls is None:
            raise EngineError(engine_name, f"Engine '{engine_name}' is not registered")

        engine_sem = self._engine_semaphores.get(engine_name)
        if engine_sem is None:
            engine_sem = asyncio.Semaphore(1)
            self._engine_semaphores[engine_name] = engine_sem

        total_acquired = False
        try:
            await asyncio.wait_for(self._total_semaphore.acquire(), timeout=30)
            total_acquired = True
            await asyncio.wait_for(engine_sem.acquire(), timeout=30)
        except asyncio.TimeoutError:
            if total_acquired:
                self._total_semaphore.release()
            raise EnginePoolExhausted(self._max_total)

        cfg = self._engine_configs.get(engine_name)
        headless = getattr(cfg, "headless", True) if cfg else True
        viewport = getattr(cfg, "viewport", None) if cfg else None
        extra_args = getattr(cfg, "extra_args", None) if cfg else None
        launch_timeout = getattr(cfg, "timeout_seconds", 30) if cfg else 30

        engine_instance: BaseEngine | None = None
        reused = False

        # ── Phase 1: try to reuse an idle engine ────────────────
        idle_list = self._idle_engines.get(engine_name, [])
        while idle_list:
            candidate = idle_list.pop()
            try:
                if await candidate.health_check():
                    engine_instance = candidate
                    reused = True
                    break
                else:
                    await candidate.close()
            except Exception:
                await candidate.close()

        # ── Phase 2: create a new engine if nothing to reuse ────
        if not reused:
            kwargs: dict = {"headless": headless, "viewport": viewport}
            if extra_args is not None:
                kwargs["extra_args"] = extra_args
            engine_instance = engine_cls(**kwargs)
            await asyncio.wait_for(engine_instance.launch(), timeout=launch_timeout)

        # Extract the user agent from the engine's page context
        user_agent = ""
        try:
            if hasattr(engine_instance, '_page') and engine_instance._page:
                user_agent = await engine_instance._page.evaluate("navigator.userAgent")
            elif hasattr(engine_instance, '_context') and engine_instance._context:
                page = await engine_instance._context.new_page()
                user_agent = await page.evaluate("navigator.userAgent")
                await page.close()
            else:
                user_agent = "Mozilla/5.0 (compatible; ApexCrawler)"
        except Exception:
            user_agent = "Mozilla/5.0 (compatible; ApexCrawler)"

        session = HybridSession(
            engine=engine_instance,
            cookies={},
            user_agent=user_agent,
        )

        try:
            self._active_engines.add(engine_instance)
            yield session
        finally:
            self._active_engines.discard(engine_instance)
            idle_pool = self._idle_engines.setdefault(engine_name, [])
            if len(idle_pool) >= self._max_idle_per_engine:
                await engine_instance.close()
            else:
                idle_pool.append(engine_instance)
            engine_sem.release()
            self._total_semaphore.release()

    async def close_all(self) -> None:
        """Close all managed engine instances and reset the pool."""
        # Close idle engines
        for engines in self._idle_engines.values():
            for eng in engines:
                try:
                    await eng.close()
                except Exception:
                    pass
        # Close engines still in use
        for eng in self._active_engines:
            try:
                await eng.close()
            except Exception:
                pass
        self._idle_engines.clear()
        self._active_engines.clear()

    async def __aenter__(self) -> EnginePool:
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close_all()
