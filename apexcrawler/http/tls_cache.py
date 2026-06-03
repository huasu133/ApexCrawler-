"""TLS session ticket cache — saves ~40ms per request by avoiding full handshake."""

import time
import logging

logger = logging.getLogger(__name__)


class TLSSessionCache:
    def __init__(self, ttl: int = 3600):
        self._cache: dict[str, tuple[bytes, float]] = {}
        self._ttl = ttl

    def get(self, host: str) -> bytes | None:
        if host in self._cache:
            ticket, ts = self._cache[host]
            if time.monotonic() - ts < self._ttl:
                return ticket
            del self._cache[host]
        return None

    def save(self, host: str, ticket: bytes):
        self._cache[host] = (ticket, time.monotonic())
        logger.debug(f"TLS session cached: {host}")
