"""Event types for the internal event bus.

Used to decouple routing/decision/engines — instead of direct imports,
modules publish/subscribe to typed events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EngineSelected:
    """Emitted when the routing layer selects an engine."""
    engine_name: str
    reason: str
    target_url: str
    trace_id: str


@dataclass(frozen=True)
class AntiCrawlSignal:
    """Emitted when anti-crawl measures are detected."""
    signal_type: str  # "captcha", "rate_limit", "block", "challenge"
    details: dict[str, Any] = field(default_factory=dict)
    target_url: str = ""
    trace_id: str = ""


@dataclass(frozen=True)
class ExtractionComplete:
    """Emitted when data extraction finishes."""
    trace_id: str
    data: dict[str, Any]
    confidence: float
    engine_used: str


@dataclass(frozen=True)
class ProxyReported:
    """Emitted when a proxy's performance is reported."""
    proxy: str
    success: bool
    latency_ms: float
    geo: str = ""


@dataclass(frozen=True)
class PipelineError:
    """Emitted when a pipeline stage fails."""
    trace_id: str
    stage_name: str
    error_type: str
    error_message: str
