"""Protocol-based cache layer.

Uses CacheBackend from core/protocols.py for dependency inversion.
Supports get/set/delete/exists/clear/invalidate operations with optional
serialization and TTL support.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from ..core.protocols import CacheBackend

logger = logging.getLogger(__name__)


class Cache:
    """High-level cache API backed by any CacheBackend implementation.

    Supports automatic JSON serialization/deserialization and TTL-based expiry.
    """

    def __init__(
        self,
        backend: CacheBackend | str = "memory",
        namespace: str = "apexcrawler",
        default_ttl: int = 3600,
        redis_url: str = "redis://localhost:6379/0",
    ):
        if isinstance(backend, str):
            backend = self._build_backend(backend, redis_url)
        self._backend = backend
        self._namespace = namespace
        self._default_ttl = default_ttl

    @staticmethod
    def _build_backend(backend_name: str, redis_url: str) -> CacheBackend:
        """Build the appropriate backend from a string identifier."""
        if backend_name == "redis":
            from .backends.redis import RedisBackend

            return RedisBackend(redis_url=redis_url)
        elif backend_name == "memory":
            from .backends.memory import MemoryBackend

            return MemoryBackend()
        else:
            raise ValueError(
                f"Unknown backend: {backend_name}. Supported: memory, redis"
            )

    def _make_key(self, key: str) -> str:
        """Namespace-prefix the cache key."""
        return f"{self._namespace}:{key}"

    async def get(self, key: str, deserialize: bool = True) -> Any | None:
        """Retrieve a cached value.

        Args:
            key: Cache key (namespace is automatically prefixed).
            deserialize: If True, attempt JSON deserialization.

        Returns:
            The cached value or None if not found / expired.
        """
        raw = await self._backend.get(self._make_key(key))
        if raw is None:
            return None
        if deserialize:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return raw
        return raw

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        serialize: bool = True,
    ) -> None:
        """Store a value in the cache.

        Args:
            key: Cache key.
            value: Value to store (dict/list/str — anything JSON-serializable).
            ttl: Time-to-live in seconds. Uses default_ttl if None.
            serialize: If True, JSON-serialize the value.
        """
        if ttl is None:
            ttl = self._default_ttl
        if serialize:
            payload = json.dumps(value, ensure_ascii=False, default=str)
        else:
            payload = value if isinstance(value, bytes) else str(value).encode()
        await self._backend.set(self._make_key(key), payload, ttl=ttl)

    async def delete(self, key: str) -> None:
        """Remove a key from the cache."""
        await self._backend.delete(self._make_key(key))

    async def exists(self, key: str) -> bool:
        """Check whether a key exists (and is not expired)."""
        return await self._backend.exists(self._make_key(key))

    async def clear(self) -> None:
        """Clear all keys in this namespace.

        This is a best-effort operation. For backends that don't support
        prefix scanning, this is a no-op.
        """
        try:
            if hasattr(self._backend, "clear_namespace"):
                await self._backend.clear_namespace(self._namespace)  # type: ignore
            else:
                logger.warning(
                    f"Backend {type(self._backend).__name__} does not support "
                    f"namespace clearing — cache may persist"
                )
        except Exception as e:
            logger.error(f"Cache clear failed: {e}")

    def namespace(self) -> str:
        return self._namespace

    def backend(self) -> CacheBackend:
        return self._backend


class CacheKeyFactory:
    """Deterministic cache key generation from URL + metadata."""

    @staticmethod
    def from_url(url: str, prefix: str = "page") -> str:
        """Generate a cache key from a URL."""
        h = hashlib.sha256(url.encode()).hexdigest()[:16]
        return f"{prefix}:{h}"

    @staticmethod
    def from_context(trace_id: str, stage: str) -> str:
        """Generate a per-stage cache key from trace context."""
        return f"ctx:{trace_id}:{stage}"

    @staticmethod
    def custom(*parts: str) -> str:
        """Generate a cache key from arbitrary string parts."""
        return ":".join(p for p in parts if p)
