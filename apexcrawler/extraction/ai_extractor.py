"""AI-powered content extraction using Crawl4AI or direct LLM."""

from __future__ import annotations
import logging
from typing import Any, TypeVar
from pydantic import BaseModel
from ..core.protocols import Extractor
from ..core.exceptions import ExtractionError

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

class AIExtractor(Extractor[T]):
    """LLM-based semantic content extractor."""
    
    def __init__(self, llm_client=None, confidence_threshold: float = 0.6):
        self._llm = llm_client
        self.confidence_threshold = confidence_threshold
    
    @property
    def confidence_threshold(self) -> float:
        return self._threshold
    
    @confidence_threshold.setter
    def confidence_threshold(self, value: float):
        self._threshold = value
    
    async def extract(self, html: str, schema: type[T]) -> T:
        """Extract structured data from HTML using LLM."""
        # Pre-process: trim large HTML
        trimmed = self._trim_html(html)
        
        if self._llm is None:
            return self._extract_without_llm(trimmed, schema)
        
        try:
            prompt = self._build_prompt(trimmed, schema)
            response = await self._llm.generate(prompt)
            return schema.model_validate_json(response)
        except Exception as e:
            raise ExtractionError(detail=str(e))
    
    def _trim_html(self, html: str) -> str:
        """Remove nav, footer, script, style elements. Reduce by ~60%."""
        import re
        for tag in ["script", "style", "nav", "footer", "noscript", "iframe"]:
            html = re.sub(f"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE)
        return html[:8000] if len(html) > 8000 else html
    
    def _build_prompt(self, html: str, schema: type[T]) -> str:
        fields = schema.model_fields
        field_desc = "\n".join(f"- {name}: {f.annotation}" for name, f in fields.items())
        return f"""Extract structured data from this HTML. Return ONLY valid JSON matching this schema:

{field_desc}

HTML:
{html}

JSON:"""
    
    def _extract_without_llm(self, html: str, schema: type[T]) -> T:
        """Fallback: try JSON-LD, meta tags, schema.org data."""
        import re, json
        # Try JSON-LD script tags
        ld_match = re.search(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if ld_match:
            try:
                data = json.loads(ld_match.group(1))
                if isinstance(data, list):
                    data = data[0] if data else {}
                return schema.model_validate(data)
            except Exception:
                pass
        raise ExtractionError(detail="No LLM available and no structured data found")
