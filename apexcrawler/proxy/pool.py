"""Proxy pool with health checks, rotation, and cooldown management.

Implements the ProxyProvider protocol from core.protocols.
Supports multiple rotation strategies: round-robin, weighted, random, least-used.

Features:
- Concurrent health checks with configurable intervals
- Cooldown tracking for failed proxies
- Atomic proxy acquisition with automatic blacklisting
- Circuit-breaker pattern for cascading failures
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

import httpx

from ..core.protocols import ProxyProvider
from ..core.exceptions import ProxyError

logger = logging.getLogger(__name__)


class RotationStrategy(Enum):
    """Proxy selection strategies."""
    ROUND_ROBIN = auto()
    RANDOM = auto()
    WEIGHTED = auto()    # Prefer proxies with higher success rate
    LEAST_USED = auto()  # Prefer proxies with fewest active connections


@dataclass
class ProxyEntry:
    """Internal representation of a proxy in the pool."""
    url: str
    protocol: str = "http"          # "http", "socks5"
    geo: str = ""                   # ISO 3166 country code
    weight: int = 1                 # Higher = more likely to be selected
    max_failures: int = 5           # Ban after this many consecutive failures

    # Runtime state
    alive: bool = True
    active_requests: int = 0
    total_requests: int = 0
    total_successes: int = 0
    total_failures: int = 0
    consecutive_failures: int = 0
    last_used: float = 0.0
    last_health_check: float = 0.0
    cooldown_until: float = 0.0
    avg_latency_ms: float = 0.0
    added_at: float = field(default_factory=time.monotonic)


class ProxyPool(ProxyProvider):
    """Thread-safe, async proxy pool with health-aware rotation.

    Usage:
        pool = ProxyPool()
        pool.add("http://user:pass@proxy1.example.com:8080", geo="US")
        pool.add("socks5://proxy2.example.com:1080", geo="DE")

        proxy = await pool.get_proxy(geo="US")
        try:
            response = await client.get(url, proxy=proxy)
            await pool.report(proxy, success=True)
        except Exception:
            await pool.report(proxy, success=False, latency_ms=5000)
    """

    def __init__(
        self,
        strategy: RotationStrategy = RotationStrategy.ROUND_ROBIN,
        health_check_interval: float = 60.0,
        cooldown_seconds: float = 300.0,
        max_concurrent_per_proxy: int = 5,
        min_pool_size: int = 1,
    ):
        self._strategy = strategy
        self._health_check_interval = health_check_interval
        self._cooldown_seconds = cooldown_seconds
        self._max_concurrent = max_concurrent_per_proxy
        self._min_pool_size = min_pool_size

        self._proxies: dict[str, ProxyEntry] = {}
        self._lock = asyncio.Lock()
        self._round_robin_idx = 0
        self._health_task: asyncio.Task | None = None

    # ── Pool Management ─────────────────────────────────────

    async def add(self, url: str, *, protocol: str = "http", geo: str = "", weight: int = 1) -> None:
        """Add a proxy to the pool.

        Args:
            url: Proxy URL (e.g. "http://user:pass@host:port" or "socks5://host:port").
            protocol: "http" or "socks5".
            geo: ISO 3166-1 alpha-2 country code.
            weight: Selection weight (higher = more likely to be selected).
        """
        async with self._lock:
            if url in self._proxies:
                logger.debug(f"Proxy already in pool: {url[:50]}...")
                return

            self._proxies[url] = ProxyEntry(
                url=url,
                protocol=protocol,
                geo=geo.upper() if geo else "",
                weight=weight,
            )
        logger.info(f"Proxy added to pool: {url[:60]}... (geo={geo or 'unknown'})")

    async def add_many(self, urls: list[str], *, protocol: str = "http", geo: str = "") -> int:
        """Add multiple proxies at once.

        Returns:
            Number of proxies actually added (excluding duplicates).
        """
        count = 0
        async with self._lock:
            for url in urls:
                if url not in self._proxies:
                    self._proxies[url] = ProxyEntry(
                        url=url,
                        protocol=protocol,
                        geo=geo.upper() if geo else "",
                    )
                    count += 1
        logger.info(f"Added {count} new proxies to pool (total: {len(self._proxies)})")
        return count

    async def remove(self, url: str) -> bool:
        """Remove a proxy from the pool permanently."""
        async with self._lock:
            removed = url in self._proxies
            self._proxies.pop(url, None)
        if removed:
            logger.warning(f"Proxy removed from pool: {url[:60]}...")
        return removed

    # ── ProxyProvider Protocol ──────────────────────────────

    async def get_proxy(self, *, geo: str | None = None) -> str:
        """Get the best available proxy, optionally matching geo.

        Args:
            geo: Desired country code (ISO 3166-1 alpha-2).

        Returns:
            Proxy URL string.

        Raises:
            ProxyError: If no healthy proxy is available.
        """
        async with self._lock:
            available = self._get_available(geo=geo)

            if not available:
                # Try again without geo restriction
                if geo:
                    available = self._get_available(geo=None)

            if not available:
                raise ProxyError("all", f"No healthy proxies available (pool size={len(self._proxies)}, live={self.alive_count})")

            selected = self._select(available)
            selected.active_requests += 1
            selected.last_used = time.monotonic()

            logger.debug(f"Selected proxy: {selected.url[:50]}... (geo={selected.geo})")
            return selected.url

    async def report(self, proxy: str, success: bool, *, latency_ms: float = 0.0) -> None:
        """Report the result of a proxy request.

        Args:
            proxy: The proxy URL that was used.
            success: Whether the request succeeded.
            latency_ms: Observed latency in milliseconds.
        """
        entry = self._proxies.get(proxy)
        if not entry:
            logger.warning(f"Report for unknown proxy: {proxy[:50]}...")
            return

        entry.total_requests += 1
        entry.active_requests = max(0, entry.active_requests - 1)

        if success:
            entry.total_successes += 1
            entry.consecutive_failures = 0
            entry.alive = True
            # Exponential moving average latency
            if entry.avg_latency_ms == 0:
                entry.avg_latency_ms = latency_ms
            else:
                alpha = 0.3
                entry.avg_latency_ms = alpha * latency_ms + (1 - alpha) * entry.avg_latency_ms
        else:
            entry.total_failures += 1
            entry.consecutive_failures += 1
            if latency_ms > 0:
                entry.avg_latency_ms = latency_ms

            if entry.consecutive_failures >= entry.max_failures:
                entry.alive = False
                entry.cooldown_until = time.monotonic() + self._cooldown_seconds
                logger.warning(
                    f"Proxy temporarily banned: {proxy[:50]}... "
                    f"(failures={entry.consecutive_failures}, cooldown={self._cooldown_seconds}s)"
                )

    async def health_check(self, proxy: str) -> bool:
        """Check if a specific proxy is healthy (can reach the internet).

        Uses a quick HTTP HEAD to a known-good endpoint.
        """
        entry = self._proxies.get(proxy)
        if not entry:
            return False

        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=10.0) as client:
                resp = await client.head("https://httpbin.org/ip")
                return resp.status_code < 500
        except Exception:
            return False

    # ── Health Check Background Task ────────────────────────

    async def start_health_checks(self) -> None:
        """Start periodic health checks in the background.

        Call at most once. Stops when the event loop shuts down.
        """
        if self._health_task is not None:
            return

        async def _loop():
            while True:
                await asyncio.sleep(self._health_check_interval)
                await self.run_all_health_checks()

        self._health_task = asyncio.ensure_future(_loop())
        logger.info(f"Health check loop started (interval={self._health_check_interval}s)")

    async def stop_health_checks(self) -> None:
        """Stop the background health check loop."""
        if self._health_task and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

    async def run_all_health_checks(self) -> tuple[int, int]:
        """Run health checks on all non-cooldown proxies.

        Returns:
            Tuple of (alive_count, dead_count).
        """
        urls = list(self._proxies.keys())
        alive, dead = 0, 0

        # Concurrent health checks
        tasks = []
        for url in urls:
            entry = self._proxies[url]
            if time.monotonic() < entry.cooldown_until:
                continue
            tasks.append(self._check_single(url))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for ok in results:
                if ok is True:
                    alive += 1
                else:
                    dead += 1

        logger.info(f"Health check complete: {alive} alive, {dead} dead (pool size={len(self._proxies)})")
        return alive, dead

    async def _check_single(self, url: str) -> bool:
        entry = self._proxies.get(url)
        if not entry:
            return False

        ok = await self.health_check(url)
        entry.last_health_check = time.monotonic()

        if ok:
            entry.alive = True
            entry.consecutive_failures = 0
            entry.cooldown_until = 0.0
        else:
            entry.consecutive_failures += 1
            if entry.consecutive_failures >= entry.max_failures:
                entry.alive = False
                entry.cooldown_until = time.monotonic() + self._cooldown_seconds

        return ok

    # ── Internal Selection ──────────────────────────────────

    def _get_available(self, geo: str | None = None) -> list[ProxyEntry]:
        """Get list of healthy, available proxies, optionally filtered by geo."""
        now = time.monotonic()
        available: list[ProxyEntry] = []
        for entry in self._proxies.values():
            if not entry.alive and now < entry.cooldown_until:
                continue
            if geo and entry.geo and entry.geo != geo.upper():
                continue
            if entry.active_requests >= self._max_concurrent:
                continue
            available.append(entry)
        return available

    def _select(self, candidates: list[ProxyEntry]) -> ProxyEntry:
        """Select the best proxy from candidates using the configured strategy."""
        if not candidates:
            raise ProxyError("all", "No candidates to select from")

        if self._strategy == RotationStrategy.ROUND_ROBIN:
            idx = self._round_robin_idx % len(candidates)
            self._round_robin_idx += 1
            return candidates[idx]

        elif self._strategy == RotationStrategy.RANDOM:
            return random.choice(candidates)

        elif self._strategy == RotationStrategy.WEIGHTED:
            total_weight = sum(e.weight for e in candidates)
            if total_weight == 0:
                return random.choice(candidates)
            r = random.uniform(0, total_weight)
            cumulative = 0.0
            for entry in candidates:
                cumulative += entry.weight
                if r <= cumulative:
                    return entry
            return candidates[-1]

        elif self._strategy == RotationStrategy.LEAST_USED:
            return min(candidates, key=lambda e: e.active_requests)

        return candidates[0]

    # ── Stats ───────────────────────────────────────────────

    @property
    def alive_count(self) -> int:
        """Number of proxies currently marked as alive."""
        now = time.monotonic()
        return sum(1 for e in self._proxies.values() if e.alive or now >= e.cooldown_until)

    @property
    def total_count(self) -> int:
        """Total number of proxies in the pool."""
        return len(self._proxies)

    def stats(self) -> dict[str, Any]:
        """Return pool statistics for monitoring."""
        entries = list(self._proxies.values())
        if not entries:
            return {"total": 0, "alive": 0}

        return {
            "total": len(entries),
            "alive": self.alive_count,
            "total_requests": sum(e.total_requests for e in entries),
            "total_successes": sum(e.total_successes for e in entries),
            "total_failures": sum(e.total_failures for e in entries),
            "success_rate": (
                sum(e.total_successes for e in entries) / max(1, sum(e.total_requests for e in entries))
            ),
            "avg_latency_ms": (
                sum(e.avg_latency_ms for e in entries if e.total_requests > 0) / max(1, sum(1 for e in entries if e.total_requests > 0))
            ),
            "geo_distribution": defaultdict(int, {e.geo: sum(1 for x in entries if x.geo == e.geo) for e in entries}),
        }
