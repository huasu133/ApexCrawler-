"""Pipeline context and crawl context dataclasses.

Context objects carry state through the entire pipeline lifecycle.
They are designed to be immutable-friendly — stages return updated copies.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class PipelineContext:
    """State carrier through all 6 pipeline stages (Schedule → Route → Evade →
    Extract → Validate → Store). Each stage reads from and writes to this context."""

    # ── Metadata ──
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    start_time: float = field(default_factory=time.monotonic)

    # ── Input ──
    target_url: str = ""
    extraction_schema: type | None = None
    priority: int = 0

    # ── Route stage outputs ──
    selected_engine: str = ""
    route_reason: str = ""
    target_difficulty: int = 0

    # ── Evade stage outputs ──
    proxy: str = ""
    user_agent: str = ""
    ja4_fingerprint: str = ""
    tls_profile: str = ""
    browser_profile: dict = field(default_factory=dict)
    webgl_renderer: str = ""
    canvas_hash: str = ""
    audio_fingerprint: str = ""
    fonts: list[str] = field(default_factory=list)

    # ── Device fingerprint fields (matching DeviceProfile) ──
    webgl_renderer: str = ""
    canvas_hash: str = ""
    audio_fingerprint: str = ""
    fonts: list[str] = field(default_factory=list)
    # ── Extract stage outputs ──
    raw_html: str = ""
    extracted_data: dict | None = None
    extraction_confidence: float = 0.0

    # ── Validate stage outputs ──
    validation_passed: bool = False
    validation_errors: list[str] = field(default_factory=list)

    # ── Store stage outputs ──
    stored_id: str = ""

    # ── Error tracking ──
    retry_count: int = 0
    stage_errors: dict[str, list[str]] = field(default_factory=dict)
    fatal_error: str = ""
    _last_status: int = 0

    def duration(self) -> float:
        return time.monotonic() - self.start_time


@dataclass
class CrawlResult:
    """Final result of a crawl pipeline execution."""

    trace_id: str
    url: str
    success: bool
    data: dict | None = None
    error: str = ""
    failed_at: str = ""
    duration_seconds: float = 0.0
    engine_used: str = ""
    proxy_used: str = ""
