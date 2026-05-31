"""In-memory cache backend implementing CacheBackend protocol.

Uses a dict with TTL-based expiry. Suitable for single-process use cases.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field



@dataclass
class _Entry:
    value: bytes
    expires_at: float  # monotonic timestamp


class MemoryBackend:
    """Dict-backed cache implementing the CacheBackend protocol.

    Supports get/set/delete/exists/incr with automatic TTL expiry.
    Thread-safe through asyncio lock.
    """

    def __init__(self, max_size: int = 10_000):
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    async def get(self, key: str) -> bytes | None:
        """Retrieve a value, returning None if expired or missing."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return entry.value

    async def set(self, key: str, value: bytes, ttl: int = 3600) -> None:
        """Store a value with TTL."""
        async with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                self._evict_one()
            self._store[key] = _Entry(
                value=value,
                expires_at=time.monotonic() + ttl,
            )

    async def delete(self, key: str) -> None:
        """Remove a key."""
        async with self._lock:
            self._store.pop(key, None)

    async def exists(self, key: str) -> bool:
        """Check whether a key exists and is not expired."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            if time.monotonic() > entry.expires_at:
                del self._store[key]
                return False
            return True

    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a numeric counter, creating it if missing."""
        async with self._lock:
            entry = self._store.get(key)
            if entry is not None and time.monotonic() <= entry.expires_at:
                current = int(entry.value)
            else:
                current = 0
            new_value = current + amount
            # Retain existing TTL or use default
            ttl = (entry.expires_at - time.monotonic()) if entry else 3600
            self._store[key] = _Entry(
                value=str(new_value).encode(),
                expires_at=time.monotonic() + ttl,
            )
            return new_value

    async def clear_namespace(self, namespace: str) -> None:
        """Remove all keys with a given prefix."""
        prefix = f"{namespace}:"
        async with self._lock:
            keys = [k for k in self._store if k.startswith(prefix)]
            for k in keys:
                del self._store[k]

    def stats(self) -> dict:
        """Return hit/miss statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._store),
            "hits": self._hits,
            "misses": self._misses,
            "hit_ratio": self._hits / total if total > 0 else 0.0,
            "max_size": self._max_size,
        }

    def _evict_one(self) -> None:
        """Evict the entry closest to expiry (simple LRU-like eviction)."""
        if not self._store:
            return
        earliest = min(self._store.items(), key=lambda kv: kv[1].expires_at)
        del self._store[earliest[0]]
