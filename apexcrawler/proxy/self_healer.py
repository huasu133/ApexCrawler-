"""Proxy self-healing: auto-switch on failure via geo-layered fallback."""

import logging
import time

from ..core.exceptions import ProxyError

logger = logging.getLogger(__name__)


class ProxySelfHealer:
    """3-level proxy switching: same geo → cross geo → alert."""

    def __init__(self, proxy_pool, alert_callback=None):
        self._pool = proxy_pool
        self._alert = alert_callback or (lambda msg: logger.warning(msg))
        self._failures: dict[str, int] = {}
        self._cooldowns: dict[str, float] = {}

    async def get_healthy_proxy(self, preferred_geo: str = "") -> str:
        """Get a working proxy with automatic failover."""
        # Try same geo
        proxy = await self._pool.get_proxy(geo=preferred_geo)
        if proxy and await self._check(proxy):
            return proxy
        # Try cross geo
        proxy = await self._pool.get_proxy(geo="")
        if proxy and await self._check(proxy):
            self._alert(f"Cross-geo fallback: {proxy}")
            return proxy
        # Alert
        self._alert("Proxy pool exhausted!")
        raise ProxyError("all", "No available proxy")

    def report_failure(self, proxy: str):
        self._failures[proxy] = self._failures.get(proxy, 0) + 1
        if self._failures[proxy] >= 3:
            self._cooldowns[proxy] = time.monotonic() + 3600  # 1h cooldown
            logger.warning(
                f"Proxy {proxy} cooldown 1h ({self._failures[proxy]} failures)"
            )

    async def _check(self, proxy: str) -> bool:
        if proxy in self._cooldowns and time.monotonic() < self._cooldowns[proxy]:
            return False
        return await self._pool.health_check(proxy)
