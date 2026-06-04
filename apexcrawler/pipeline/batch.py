"""Batch pipeline executor: process multiple URLs sharing engine/connection pool."""

from __future__ import annotations

import asyncio
import logging
from ..core.context import PipelineContext
from .core import PipelineExecutor, StageConfig

logger = logging.getLogger(__name__)


class BatchPipelineExecutor:
    """Process multiple URLs through the same pipeline, reusing engine instances.
    
    Shares connection pools, DNS cache, and browser contexts across URLs
    for the same domain, dramatically reducing per-request overhead.
    """
    
    def __init__(self, executor: PipelineExecutor, max_concurrent: int = 3):
        self._executor = executor
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
    
    async def run_batch(
        self, urls: list[str], base_ctx: PipelineContext | None = None
    ) -> list[tuple[bool, PipelineContext]]:
        """Process multiple URLs with concurrency control.
        
        Args:
            urls: List of target URLs to crawl.
            base_ctx: Template PipelineContext (optional). Each URL gets a copy.
            
        Returns:
            List of (success, ctx) tuples in input order.
        """
        async def _process(url: str) -> tuple[bool, PipelineContext]:
            async with self._semaphore:
                ctx = PipelineContext(target_url=url)
                if base_ctx:
                    ctx.extraction_schema = base_ctx.extraction_schema
                    ctx.selected_engine = base_ctx.selected_engine
                try:
                    return await self._executor.run(ctx)
                except Exception as e:
                    logger.error(f"Batch task failed for {url}: {e}")
                    ctx.fatal_error = str(e)
                    return False, ctx
        
        tasks = [_process(url) for url in urls]
        return await asyncio.gather(*tasks)
