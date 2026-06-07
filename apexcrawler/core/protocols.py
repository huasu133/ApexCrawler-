"""Protocol definitions (structural subtyping) for ApexCrawler.

All major components communicate through these protocols,
enabling dependency inversion and testability.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable, TypeVar, Generic, Any, Optional

T = TypeVar("T", covariant=True)


# ── Browser Engine ──────────────────────────────────────────

class PageInteractionsMixin:
    """Default interaction implementations using evaluate() fallback.

    引擎如果原生支持交互（如 Playwright），应该覆盖这些方法以获得更好的效果。
    不支持原生交互的引擎（如 vanilla），会通过 JS evaluate() 降级。
    """

    async def click(self, selector: str, **kwargs) -> None:
        await self.evaluate(f"document.querySelector('{selector}')?.click()")

    async def fill(self, selector: str, value: str, **kwargs) -> None:
        escaped = value.replace("'", "\\'")
        await self.evaluate(f"document.querySelector('{selector}')?.value = '{escaped}'")

    async def scroll(self, x: int = 0, y: int = 500) -> None:
        await self.evaluate(f"window.scrollTo({x}, {y})")

    async def wait_for_selector(self, selector: str, timeout: int = 5000) -> Any:
        import asyncio
        deadline = asyncio.get_event_loop().time() + timeout / 1000
        while asyncio.get_event_loop().time() < deadline:
            result = await self.evaluate(f"document.querySelector('{selector}') !== null")
            if result:
                return True
            await asyncio.sleep(0.1)
        return False

    async def get_attribute(self, selector: str, attr: str) -> Optional[str]:
        return await self.evaluate(f"document.querySelector('{selector}')?.getAttribute('{attr}')")

    async def text_content(self, selector: str) -> Optional[str]:
        return await self.evaluate(f"document.querySelector('{selector}')?.textContent")

    async def press(self, key: str) -> None:
        await self.evaluate(f"""
            document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', {{key: '{key}'}}));
            document.activeElement?.dispatchEvent(new KeyboardEvent('keyup', {{key: '{key}'}}));
        """)

    async def hover(self, selector: str) -> None:
        await self.evaluate(f"""
            const el = document.querySelector('{selector}');
            if (el) {{
                el.dispatchEvent(new MouseEvent('mouseover', {{bubbles: true}}));
                el.dispatchEvent(new MouseEvent('mouseenter', {{bubbles: true}}));
            }}
        """)

    async def select_option(self, selector: str, value: str) -> None:
        await self.evaluate(f"""
            const el = document.querySelector('{selector}');
            if (el) {{ el.value = '{value}'; el.dispatchEvent(new Event('change', {{bubbles: true}})); }}
        """)


@runtime_checkable
class Page(PageInteractionsMixin, Protocol):
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
