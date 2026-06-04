"""Built-in plugins for ApexCrawler."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import Plugin

logger = logging.getLogger(__name__)


class LoggingPlugin(Plugin):
    """Logs key pipeline events for debugging."""

    name = "logging"

    async def on_post_extract(self, ctx: Any) -> None:
        logger.info(
            f"[plugin:logging] extracted {len(ctx.raw_html or '')}B "
            f"from {ctx.target_url} via {ctx.selected_engine}"
        )

    async def on_error(self, ctx: Any, exc: Exception) -> None:
        logger.error(
            f"[plugin:logging] pipeline error for {ctx.target_url}: {exc}"
        )


class JSONExportPlugin(Plugin):
    """Exports extracted data to a JSON file after each crawl."""

    name = "json_export"

    def __init__(self, output_dir: str = "crawl_results"):
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def on_pre_store(self, ctx: Any) -> None:
        if not ctx.raw_html:
            return
        trace_id = getattr(ctx, "trace_id", "unknown")
        output_file = self._output_dir / f"{trace_id}.json"

        data = {
            "url": ctx.target_url,
            "trace_id": trace_id,
            "engine": ctx.selected_engine,
            "html_bytes": len(ctx.raw_html or ""),
            "confidence": ctx.extraction_confidence,
            "valid": ctx.validation_passed,
            "errors": ctx.validation_errors,
        }
        output_file.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        logger.info(f"[plugin:json_export] saved to {output_file}")


class RetryAlertPlugin(Plugin):
    """Alerts when retry count exceeds threshold — for monitoring/alerting."""

    name = "retry_alert"

    def __init__(self, max_retries: int = 3):
        self._max_retries = max_retries

    async def on_error(self, ctx: Any, exc: Exception) -> None:
        retries = getattr(ctx, "retry_count", 0)
        if retries >= self._max_retries:
            logger.warning(
                f"[plugin:retry_alert] {ctx.target_url} failed after "
                f"{retries} retries: {exc}"
            )
