"""Abstract base engine and capability model for ApexCrawler.

Defines the BaseEngine ABC that all engine implementations must subclass
and the EngineCapability dataclass used for routing decisions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apexcrawler.core.protocols import Page


@dataclass(frozen=True)
class EngineCapability:
    """Describes the capabilities of a browser engine.

    Used by the routing layer to match engines to target difficulty levels.
    All scores are on a 1-10 scale (higher = better).
    """

    name: str
    fingerprint_resistance: int = 5
    ja4_diversity: int = 5
    dom_automation: int = 5
    resource_cost: int = 5
    supports_webgpu: bool = False
    supports_wasm_intercept: bool = False
    supports_cdp_hide: bool = False
    tags: list[str] = field(default_factory=list)


class BaseEngine(ABC):
    """Abstract base for all browser engines.

    Subclasses must implement capability(), launch(), navigate(), and close().
    The health_check() method has a default no-op implementation.
    """

    @classmethod
    @abstractmethod
    def capability(cls) -> EngineCapability:
        """Return the static capability descriptor for this engine."""
        ...

    @abstractmethod
    async def launch(self) -> None:
        """Launch the browser engine and prepare for navigation."""
        ...

    @abstractmethod
    async def navigate(self, url: str, proxy: str | None = None) -> Page:
        """Navigate to a URL and return a Page protocol object.

        Args:
            url: The target URL to navigate to.
            proxy: Optional proxy string (e.g. "http://user:pass@host:port").

        Returns:
            A Page object conforming to the Page protocol.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the browser engine and release all resources."""
        ...

    async def health_check(self) -> bool:
        """Check if the engine is healthy and ready.

        Returns:
            True if the engine is operational, False otherwise.
        """
        return True
