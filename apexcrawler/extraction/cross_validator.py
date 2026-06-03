from __future__ import annotations
"""Multi-source cross-validation: JSON-LD + LLM + Microdata voting."""

import json
import logging
import re
from collections import Counter

logger = logging.getLogger(__name__)


class CrossValidator:
    """Voting-based cross-validation from multiple extraction sources."""

    SOURCES = ["json_ld", "microdata", "opengraph", "meta", "llm"]

    async def validate(self, html: str, field: str, llm_value=None) -> dict:
        sources = {}
        sources["json_ld"] = self._from_jsonld(html, field)
        sources["opengraph"] = self._from_og(html, field)
        sources["meta"] = self._from_meta(html, field)
        if llm_value:
            sources["llm"] = llm_value

        values = [v for v in sources.values() if v is not None]
        if not values:
            return {"value": None, "confidence": 0.0, "sources_agree": 0}

        counts = Counter(values)
        best_value, best_count = counts.most_common(1)[0]
        return {
            "value": best_value,
            "confidence": best_count / len(values) if values else 0,
            "sources_agree": best_count,
            "all_sources": sources,
        }

    def _from_jsonld(self, html: str, field: str) -> str | None:
        m = re.search(
            r'script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        )
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list):
                    data = data[0]
                return str(data.get(field)) if field in data else None
            except Exception:
                pass
        return None

    def _from_og(self, html: str, field: str) -> str | None:
        m = re.search(
            f'<meta[^>]*property="og:{field}"[^>]*content="([^"]*)"', html
        )
        return m.group(1) if m else None

    def _from_meta(self, html: str, field: str) -> str | None:
        m = re.search(f'<meta[^>]*name="{field}"[^>]*content="([^"]*)"', html)
        return m.group(1) if m else None
