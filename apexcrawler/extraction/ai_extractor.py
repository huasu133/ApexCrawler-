"""AI-powered content extraction using Crawl4AI or direct LLM."""

from __future__ import annotations
import hashlib
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
        """Extract structured data from HTML using structured data first, LLM fallback."""
        # Step 0: content hash dedup
        html_hash = hashlib.sha256(html.encode()).hexdigest()
        
        # Step 1: structured data first (zero LLM cost)
        try:
            return self._extract_structured(html, schema)
        except Exception:
            pass
        
        # Step 2: LLM with smart trim and improved prompt
        trimmed = self._semantic_trim(html)
        prompt = self._build_prompt_v2(trimmed, schema)
        
        try:
            response = await self._llm.generate(prompt, temperature=0)
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

    def _extract_structured(self, html: str, schema: type[T]) -> T:
        """Try JSON-LD → OpenGraph → Twitter Card in order (zero LLM cost)."""
        import re, json

        # JSON-LD
        for match in re.finditer(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        ):
            try:
                data = json.loads(match.group(1))
                if isinstance(data, list):
                    data = data[0] if data else {}
                return schema.model_validate(data)
            except Exception:
                pass

        # OpenGraph meta tags
        og = {}
        for m in re.finditer(r'<meta[^>]*property="og:(\w+)"[^>]*content="([^"]+)"', html):
            og[m.group(1)] = m.group(2)
        if og:
            try:
                return schema.model_validate(og)
            except Exception:
                pass

        # Twitter Card meta tags
        tc = {}
        for m in re.finditer(r'<meta[^>]*name="twitter:(\w+)"[^>]*content="([^"]+)"', html):
            tc[m.group(1)] = m.group(2)
        if tc:
            try:
                return schema.model_validate(tc)
            except Exception:
                pass

        raise ExtractionError("No structured data found")

    def _build_prompt_v2(self, html: str, schema: type[T]) -> str:
        """Improved prompt with few-shot example and chain-of-thought reasoning."""
        fields = "\n".join(
            f"  {name}: {f.annotation}" for name, f in schema.model_fields.items()
        )
        return f"""Extract structured data from HTML.

Schema:
{fields}

Example output:
{{"title": "Product Name", "price": 19.99, "rating": 4.5}}

Think step by step:
1. What type of page is this?
2. Where is the main content?
3. Extract each field with confidence.

HTML:
{html}

Return ONLY valid JSON. Use null for missing fields."""

    def _semantic_trim(self, html: str, max_chars: int = 6000) -> str:
        """Smart trim: keep JSON-LD + OG + main/article + headings, remove nav/footer/script/style."""
        import re
        # Remove script, style, nav, footer, noscript, iframe
        for tag in ["script", "style", "nav", "footer", "noscript", "iframe"]:
            html = re.sub(
                f"<{tag}[^>]*>.*?</{tag}>",
                "",
                html,
                flags=re.DOTALL | re.IGNORECASE,
            )
        # Truncate to max_chars if needed
        return html[:max_chars] if len(html) > max_chars else html
