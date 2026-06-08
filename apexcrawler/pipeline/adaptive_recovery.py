"""Adaptive recovery: automatically probe and recover from degraded states."""

import asyncio
import time
import logging
logger = logging.getLogger(__name__)

class AdaptiveRecoveryManager:
    """Periodically probes to check if targets have recovered from rate limiting."""
    
    def __init__(self, probe_interval: int = 300, max_probes: int = 3):
        self._interval = probe_interval
        self._max_probes = max_probes
        self._degraded: dict[str, dict] = {}  # domain -> {timestamp, probes, engine}
    
    def mark_degraded(self, domain: str, reason: str, current_engine: str = ""):
        if domain not in self._degraded:
            self._degraded[domain] = {
                "since": time.monotonic(),
                "reason": reason,
                "probes": 0,
                "original_engine": current_engine,
            }
    
    async def should_recover(self, domain: str) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if domain not in self._degraded:
            return False
        info = self._degraded[domain]
        elapsed = time.monotonic() - info["since"]
        if elapsed < self._interval:
            return False
        return info["probes"] < self._max_probes
    
    async def probe(self, domain: str, fetch_func) -> bool:
        """Attempt a recovery probe. Returns True if recovered."""
        try:
            info = self._degraded[domain]
            info["probes"] += 1
            response = await fetch_func(f"https://{domain}")
            if response and len(response) > 500:
                logger.info("Recovery probe [%s] success", domain)
                del self._degraded[domain]
                return True
        except Exception as e:
            logger.debug("Recovery probe [%s] failed: %s", domain, e)
        return False
    
    def is_degraded(self, domain: str) -> bool:
        return domain in self._degraded
    
    def reset(self, domain: str):
        self._degraded.pop(domain, None)
