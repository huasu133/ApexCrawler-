"""Structured logging for ApexCrawler using structlog.

Provides a logger factory that returns structlog.BoundLogger instances
configured for different components (pipeline stages, engines, anti-crawl, etc.).

Usage:
    from apexcrawler.utils.logger import get_logger
    log = get_logger("extraction.ai_extractor")
    log.info("extraction_complete", schema="Product", fields=5, duration_ms=123.4)
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def configure_logging(
    level: str = "INFO",
    *,
    json_format: bool = False,
    pretty_print: bool = True,
    log_file: str = "",
    include_processors: list | None = None,
) -> None:
    """Configure structlog globally for the ApexCrawler application.

    Call once at startup, usually from the CLI entry point.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_format: Output JSON lines (for production/ELK).
        pretty_print: Use colored console output (for development).
        log_file: Optional file path for log output.
        include_processors: Additional structlog processors.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Shared processors
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.dev.set_exc_info,
    ]

    if include_processors:
        shared_processors.extend(include_processors)

    # Renderer
    if json_format:
        shared_processors.append(structlog.processors.JSONRenderer())
    elif pretty_print and sys.stderr.isatty():
        shared_processors.append(structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=structlog.dev.rich_traceback,
        ))
    else:
        shared_processors.append(structlog.dev.ConsoleRenderer(colors=False))

    # Configure structlog
    structlog.configure(
        processors=shared_processors,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging (for libraries that use it)
    handlers: list[logging.Handler] = []

    if pretty_print and sys.stderr.isatty():
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(numeric_level)
        handlers.append(console_handler)
    else:
        handlers.append(logging.StreamHandler(sys.stderr))

    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(numeric_level)
        handlers.append(file_handler)

    logging.basicConfig(
        format="%(message)s",
        level=numeric_level,
        handlers=handlers,
        force=True,
    )

    # Suppress noisy third-party loggers
    for noisy in (
        "httpx", "httpcore", "urllib3", "asyncio",
        "selenium", "playwright", "PIL", "fontTools",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Log confirmation
    log = structlog.get_logger("apexcrawler")
    log.info("logging_configured", level=level, json=json_format, file=log_file or "stderr")


def get_logger(name: str, **context: Any) -> structlog.BoundLogger:
    """Get a structlog logger bound to a component name and optional context.

    Args:
        name: Logger name, conventionally "module.component" (e.g., "proxy.pool").
        **context: Additional key-value pairs bound to all log entries.

    Returns:
        A structlog.BoundLogger with the given context pre-bound.
    """
    return structlog.get_logger(f"apexcrawler.{name}").bind(**context)


# ── Pre-configured loggers for common components ───────────

_default_context: dict[str, Any] = {}


def pipeline_logger(stage_name: str) -> structlog.BoundLogger:
    """Get a logger for a specific pipeline stage."""
    return get_logger(f"pipeline.{stage_name}", stage=stage_name)


def engine_logger(engine_name: str) -> structlog.BoundLogger:
    """Get a logger for a specific browser engine."""
    return get_logger(f"engines.{engine_name}", engine=engine_name)


def proxy_logger(provider: str = "pool") -> structlog.BoundLogger:
    """Get a logger for the proxy subsystem."""
    return get_logger(f"proxy.{provider}", component="proxy")


def extraction_logger(extractor: str = "ai") -> structlog.BoundLogger:
    """Get a logger for the extraction subsystem."""
    return get_logger(f"extraction.{extractor}", component="extraction")


def anti_crawl_logger(vendor: str = "") -> structlog.BoundLogger:
    """Get a logger for anti-crawl detection/evasion."""
    ctx = {"component": "anti_crawl"}
    if vendor:
        ctx["vendor"] = vendor
    return get_logger("decision", **ctx)


# ── Sensitive Data Masking ─────────────────────────────────

_SENSITIVE_KEYS = frozenset({
    "proxy", "proxy_url", "api_key", "password", "token", "secret",
    "authorization", "cookie", "set-cookie", "x-api-key",
})


def mask_sensitive(data: dict[str, Any]) -> dict[str, Any]:
    """Mask sensitive values in a dict for safe logging.

    Args:
        data: Dict potentially containing sensitive keys.

    Returns:
        New dict with sensitive values replaced by "***".
    """
    masked: dict[str, Any] = {}
    for key, value in data.items():
        if key.lower() in _SENSITIVE_KEYS:
            masked[key] = "***"
        elif isinstance(value, dict):
            masked[key] = mask_sensitive(value)
        elif isinstance(value, list):
            masked[key] = [
                mask_sensitive(v) if isinstance(v, dict) else ("***" if key.lower() in _SENSITIVE_KEYS else v)
                for v in value
            ]
        else:
            masked[key] = value
    return masked


class SensitiveFilter(logging.Filter):
    """Logging filter that masks sensitive header/cookie values."""
    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg") and isinstance(record.msg, dict):
            record.msg = mask_sensitive(record.msg)  # type: ignore
        return True
