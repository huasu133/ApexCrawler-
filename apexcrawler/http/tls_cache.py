"""TLS session ticket cache — saves ~40ms per request by avoiding full handshake."""

from __future__ import annotations

import time
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)


class TLSSessionCache:
    def __init__(self, ttl: int = 3600, max_size: int = 1000):
        self._cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._ttl = ttl
        self._max_size = max_size

    def get(self, host: str) -> bytes | None:
        if host in self._cache:
            ticket, ts = self._cache[host]
            if time.monotonic() - ts < self._ttl:
                self._cache.move_to_end(host)
                return ticket
            del self._cache[host]
        return None

    def save(self, host: str, ticket: bytes):
        if host in self._cache:
            del self._cache[host]
        self._cache[host] = (ticket, time.monotonic())
        # LRU eviction: remove oldest entries when over max_size
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)
        logger.debug(f"TLS session cached: {host}")
