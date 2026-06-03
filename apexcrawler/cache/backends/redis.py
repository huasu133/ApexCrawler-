"""Redis-backed cache implementing CacheBackend protocol.

Uses redis-py with connection pooling. Falls back gracefully if redis is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class RedisBackend:
    """Redis cache backend implementing the CacheBackend protocol.

    Supports get/set/delete/exists/incr with connection pooling and
    optional JSON serialization.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        pool_size: int = 10,
        key_prefix: str = "apexcrawler",
    ):
        self._redis_url = redis_url
        self._pool_size = pool_size
        self._key_prefix = key_prefix
        self._client = None
        self._ready = False

    async def _ensure_client(self):
        if self._client is not None:
            return
        try:
            import redis.asyncio as aioredis

            self._client = aioredis.from_url(
                self._redis_url,
                max_connections=self._pool_size,
                decode_responses=False,
            )
            await self._client.ping()
            self._ready = True
            logger.info(f"RedisBackend connected to {self._redis_url}")
        except Exception as e:
            logger.error(f"RedisBackend connection failed: {e}")
            raise

    async def get(self, key: str) -> bytes | None:
        """Retrieve a value from Redis."""
        if not self._ready:
            await self._ensure_client()
        try:
            return await self._client.get(self._prefixed(key))
        except Exception as e:
            logger.warning(f"Redis GET failed for {key}: {e}")
            return None

    async def set(self, key: str, value: bytes, ttl: int = 3600) -> None:
        """Store a value in Redis with TTL."""
        if not self._ready:
            await self._ensure_client()
        try:
            await self._client.setex(self._prefixed(key), ttl, value)
        except Exception as e:
            logger.warning(f"Redis SET failed for {key}: {e}")

    async def delete(self, key: str) -> None:
        """Remove a key from Redis."""
        if not self._ready:
            await self._ensure_client()
        try:
            await self._client.delete(self._prefixed(key))
        except Exception as e:
            logger.warning(f"Redis DELETE failed for {key}: {e}")

    async def exists(self, key: str) -> bool:
        """Check whether a key exists in Redis."""
        if not self._ready:
            await self._ensure_client()
        try:
            return bool(await self._client.exists(self._prefixed(key)))
        except Exception as e:
            logger.warning(f"Redis EXISTS failed for {key}: {e}")
            return False

    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a counter in Redis."""
        if not self._ready:
            await self._ensure_client()
        try:
            return await self._client.incrby(self._prefixed(key), amount)
        except Exception as e:
            logger.warning(f"Redis INCR failed for {key}: {e}")
            return 0

    async def clear_namespace(self, namespace: str) -> None:
        """Remove all keys with a given prefix using SCAN."""
        if not self._ready:
            await self._ensure_client()
        prefix = self._prefixed(f"{namespace}")
        try:
            cursor = 0
            while True:
                cursor, keys = await self._client.scan(
                    cursor, match=f"{prefix}:*", count=100
                )
                if keys:
                    await self._client.delete(*keys)
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning(f"Redis clear_namespace failed: {e}")

    async def close(self) -> None:
        """Close the Redis connection pool."""
        if self._client:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
            self._ready = False

    def _prefixed(self, key: str) -> str:
        return f"{self._key_prefix}:{key}"
