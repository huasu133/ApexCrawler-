"""Parallel zone for pipeline — Evade + Extract can run concurrently."""

import asyncio, logging
from ..core.context import PipelineContext

logger = logging.getLogger(__name__)

class ParallelZone:
    """Group of stages executed concurrently via asyncio.gather."""
    
    def __init__(self, stages: list, name: str = ""):
        self._stages = stages
        self.name = name or f"parallel_{id(self)}"
    
    async def execute(self, *contexts: PipelineContext) -> list[PipelineContext]:
        """Execute all stages in parallel, one per context."""
        tasks = []
        for i, ctx in enumerate(contexts):
            stage = self._stages[i % len(self._stages)]
            tasks.append(stage.execute(ctx))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        output = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Parallel zone [{self.name}] failed: {result}")
                contexts[i].stage_errors.setdefault(self.name, []).append(str(result))
                output.append(contexts[i])
            else:
                output.append(result)
        return output
