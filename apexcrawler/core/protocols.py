"""Protocol definitions (structural subtyping) for ApexCrawler.

All major components communicate through these protocols,
enabling dependency inversion and testability.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable, TypeVar, Generic, Any

T = TypeVar("T", covariant=True)


# ── Browser Engine ──────────────────────────────────────────

@runtime_checkable
class Page(Protocol):
    """Abstract browser page."""

    @property
    def content(self) -> str: ...

    @property
    def url(self) -> str: ...

    async def evaluate(self, script: str) -> Any: ...

    async def screenshot(self, *, full_page: bool = False) -> bytes: ...

    async def close(self) -> None: ...


@runtime_checkable
class Engine(Protocol):
    """Abstract browser engine."""

    name: str

    @classmethod
    def capability(cls) -> dict[str, Any]: ...

    async def launch(self) -> None: ...

    async def new_page(self, *, proxy: str | None = None) -> Page: ...

    async def close(self) -> None: ...

    async def health_check(self) -> bool: ...


# ── Extraction ──────────────────────────────────────────────

@runtime_checkable
class Extractor(Protocol, Generic[T]):
    """Abstract data extractor."""

    async def extract(self, html: str, schema: type[T]) -> T: ...

    @property
    def confidence_threshold(self) -> float: ...


# ── Cache ───────────────────────────────────────────────────

@runtime_checkable
class CacheBackend(Protocol):
    """Abstract cache backend."""

    async def get(self, key: str) -> bytes | None: ...

    async def set(self, key: str, value: bytes, ttl: int = 3600) -> None: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def incr(self, key: str, amount: int = 1) -> int: ...


# ── Proxy ───────────────────────────────────────────────────

@runtime_checkable
class ProxyProvider(Protocol):
    """Abstract proxy provider."""

    async def get_proxy(self, *, geo: str | None = None) -> str: ...

    async def report(self, proxy: str, success: bool) -> None: ...

    async def health_check(self, proxy: str) -> bool: ...


# ── Pipeline ────────────────────────────────────────────────

@runtime_checkable
class PipelineStage(Protocol):
    """Single stage in the crawl pipeline."""

    @property
    def name(self) -> str: ...

    async def execute(self, ctx: "PipelineContext") -> "PipelineContext": ...

    async def rollback(self, ctx: "PipelineContext") -> None: ...


# ── Decision ────────────────────────────────────────────────

@runtime_checkable
class DecisionEngine(Protocol):
    """Anti-crawl detection and strategy selection."""

    async def analyze(self, html: str, headers: dict[str, str], status: int) -> dict[str, Any]: ...

    async def recommend(self, url: str) -> dict[str, Any]: ...
