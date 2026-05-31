"""Engine registry with decorator-based registration.

Allows engine implementations to self-register via the @EngineRegistry.register
class decorator. The registry is used by the routing layer to discover
available engines and select the best match.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from apexcrawler.engines.base import BaseEngine, EngineCapability


class EngineRegistry:
    """Global registry for browser engine implementations.

    Engines register themselves via the @EngineRegistry.register decorator.
    The registry is then queried by the routing layer to find available
    engines matching a target difficulty profile.
    """

    _engines: dict[str, type[BaseEngine]] = {}

    @classmethod
    def register(cls, engine_cls: type[BaseEngine]) -> type[BaseEngine]:
        """Decorator to register an engine class.

        Usage:
            @EngineRegistry.register
            class VanillaEngine(BaseEngine):
                ...
        """
        name = engine_cls.capability().name
        cls._engines[name] = engine_cls
        return engine_cls

    @classmethod
    def get(cls, name: str) -> type[BaseEngine] | None:
        """Get a registered engine class by name."""
        return cls._engines.get(name)

    @classmethod
    def list_all(cls) -> dict[str, type[BaseEngine]]:
        """Return a copy of all registered engines."""
        return dict(cls._engines)

    @classmethod
    def list_capabilities(cls) -> dict[str, EngineCapability]:
        """Return capability descriptors for all registered engines."""
        return {name: eng.capability() for name, eng in cls._engines.items()}

    @classmethod
    def clear(cls) -> None:
        """Clear all registrations (mainly for testing)."""
        cls._engines.clear()
