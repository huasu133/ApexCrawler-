"""
ApexCrawler SDK — decorator-based scraping interface.

Usage:
    from apexcrawler import browser, request

    @browser(engine="auto", cache_ttl=3600)
    def scrape_product(url: str) -> dict:
        '''Scrape a product page. Browser lifecycle auto-managed.'''
        ...

    @request(impersonate="chrome131", retry=3)
    def fetch_api(url: str) -> dict:
        '''Quick API fetch. Auto-proxied, auto-retried.'''
        ...
"""
from __future__ import annotations
import asyncio
import functools
import ipaddress
import json
import logging
import socket
import time
from typing import Any, Callable
from urllib.parse import urlparse

from apexcrawler.core.context import PipelineContext
from apexcrawler.pipeline.stages import (
    ScheduleStage, RouteStage, EvadeStage, ExtractStage, ValidateStage, StoreStage,
)
from apexcrawler.pipeline.core import PipelineExecutor, StageConfig, RetryPolicy

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# SSRF Protection
# ══════════════════════════════════════════════════════════════════════

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_url(url: str) -> None:
    """Validate URL to prevent SSRF attacks. Raises ValueError if blocked."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Blocked scheme: {parsed.scheme}")
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError(f"Blocked host: {host}")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP — resolve via DNS
        try:
            resolved = socket.gethostbyname(host)
            addr = ipaddress.ip_address(resolved)
        except socket.gaierror:
            return  # Cannot resolve — let the caller handle
    else:
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise ValueError(f"Blocked IP: {host} (in {net})")


class SDKRuntime:
    """Singleton runtime shared across SDK calls."""
    _instance: SDKRuntime | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


def browser(engine: str = "auto", proxy: str | None = None, headless: bool = True,
            cache_ttl: int = 0, output: str = "json"):
    """Decorator: scrape with browser engine auto-lifecycle."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Build pipeline stages
            stages = [ScheduleStage(), RouteStage(), EvadeStage(), ExtractStage(), ValidateStage(), StoreStage()]
            configs = {
                "schedule": StageConfig(timeout=5.0),
                "route": StageConfig(timeout=10.0),
                "evade": StageConfig(timeout=10.0),
                "extract": StageConfig(timeout=60.0, retry=RetryPolicy(max_retries=2, base_delay=2.0)),
                "validate": StageConfig(timeout=10.0),
                "store": StageConfig(timeout=10.0),
            }
            executor = PipelineExecutor(stages=stages, configs=configs)

            # Build context from the first positional arg (should be URL)
            target_url = args[0] if args else kwargs.get('url', '')
            _validate_url(target_url)
            ctx = PipelineContext(target_url=target_url)
            if engine and engine != "auto":
                ctx.selected_engine = engine

            success, result_ctx = await executor.run(ctx)

            if not success:
                error = result_ctx.fatal_error or result_ctx.stage_errors
                result = {"url": target_url, "success": False, "error": str(error)}
            else:
                # Pass the raw HTML and crawl4ai content through to the decorated function
                # via the context
                if asyncio.iscoroutinefunction(func):
                    result = await func(ctx=result_ctx, *args, **kwargs)
                else:
                    result = func(ctx=result_ctx, *args, **kwargs)

                if result is None:
                    result = {
                        "url": target_url, "success": True,
                        "html_length": len(result_ctx.raw_html or ""),
                        "engine": result_ctx.selected_engine,
                        "confidence": result_ctx.extraction_confidence,
                        "crawl4ai_content": result_ctx.raw_crawl4ai[:50000] if result_ctx.raw_crawl4ai else "",
                    }

            if output == "json":
                return json.dumps(result, ensure_ascii=False, default=str)
            return result
        return wrapper
    return decorator


def request(impersonate: str = "chrome131", retry: int = 3, proxy: str | None = None,
            cache_ttl: int = 0, output: str = "json"):
    """Decorator: lightweight HTTP request with TLS impersonation."""
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            from apexcrawler.http.fetcher import FastFetcher

            target_url = args[0] if args else kwargs.get('url', '')
            _validate_url(target_url)

            fetcher = FastFetcher(impersonate=impersonate, proxy=proxy)
            try:
                result = fetcher.get(target_url)
                status = result.get("status_code", 0)
                text = result.get("text", "")

                if status != 200:
                    return json.dumps({"url": target_url, "status_code": status, "error": f"HTTP {status}"}, ensure_ascii=False)

                if asyncio.iscoroutinefunction(func):
                    resp = await func(response=result, *args, **kwargs)
                else:
                    resp = func(response=result, *args, **kwargs)

                if resp is None:
                    resp = {"url": target_url, "status_code": status, "content_length": len(text)}

                if output == "json":
                    return json.dumps(resp, ensure_ascii=False, default=str)
                return resp
            finally:
                fetcher.close()
        return wrapper
    return decorator
