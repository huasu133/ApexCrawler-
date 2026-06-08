"""Multi-source cross-validation: JSON-LD + LLM + Microdata voting."""
from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


class CrossValidator:
    """Voting-based cross-validation from multiple extraction sources."""

    SOURCES = ["json_ld", "microdata", "opengraph", "meta", "llm"]

    SOURCE_WEIGHTS = {
        "json_ld": 5,
        "microdata": 4,
        "opengraph": 3,
        "llm": 2,
        "meta": 1,
    }

    _CLUSTER_THRESHOLD = 0.5

    async def validate(self, html: str, field: str, llm_value=None) -> dict:
        sources = {}
        sources["json_ld"] = self._from_jsonld(html, field)
        sources["microdata"] = self._from_microdata(html, field)
        sources["opengraph"] = self._from_og(html, field)
        sources["meta"] = self._from_meta(html, field)
        if llm_value:
            sources["llm"] = llm_value

        # Filter to sources with a non-None value
        valid_sources = {k: v for k, v in sources.items() if v is not None}
        if not valid_sources:
            return {"value": None, "confidence": 0.0, "sources_agree": 0}

        # Fuzzy clustering: group similar values using Jaccard similarity
        clusters = []  # list of (representative_value, [values], weight)
        for src_name, val in valid_sources.items():
            s = str(val)
            placed = False
            for cluster in clusters:
                cluster_val, cluster_vals, _cluster_weight = cluster
                if self._jaccard_similarity(s, str(cluster_val)) > self._CLUSTER_THRESHOLD:
                    cluster_vals.append(s)
                    placed = True
                    break
            if not placed:
                clusters.append([s, [s], 0])

        # Assign weights: sum source weights for each cluster
        for cluster in clusters:
            _rep, cluster_vals, _w = cluster
            total_weight = 0
            for cval in cluster_vals:
                for src_name, src_val in valid_sources.items():
                    if str(src_val) == cval:
                        total_weight += self.SOURCE_WEIGHTS.get(src_name, 1)
            cluster[2] = total_weight

        # Choose cluster with highest weighted score; break ties by count
        clusters.sort(key=lambda c: (c[2], len(c[1])), reverse=True)
        best_value, best_vals, _best_weight = clusters[0]
        total_sources = len(valid_sources)

        return {
            "value": best_value,
            "confidence": round(len(best_vals) / total_sources, 2),
            "sources_agree": len(best_vals),
            "all_sources": sources,
        }

    def _jaccard_similarity(self, a: str, b: str) -> float:
        """Jaccard similarity based on word-level token sets."""
        set_a = set(a.lower().split())
        set_b = set(b.lower().split())
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        union = set_a | set_b
        return len(intersection) / len(union)

    def _from_microdata(self, html: str, field: str) -> str | None:
        """Extract value from HTML Microdata (itemscope/itemprop attributes)."""
        pattern = rf'<[^>]+itemprop=["\']?{re.escape(field)}["\']?[^>]*>([^<]*)</'
        for m in re.finditer(pattern, html, re.IGNORECASE):
            value = m.group(1).strip()
            if value:
                return value
        return None

    def _from_jsonld(self, html: str, field: str) -> str | None:
        for m in re.finditer(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            re.DOTALL,
        ):
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list):
                    data = data[0]
                if field in data:
                    return str(data.get(field))
            except Exception as e:
                logger.debug(f"field extraction failed: {e}")
        return None

    def _from_og(self, html: str, field: str) -> str | None:
        m = re.search(
            rf'<meta[^>]*property="og:{field}"[^>]*content="((?:[^"\\]|\\.)*)"', html
        )
        return m.group(1) if m else None

    def _from_meta(self, html: str, field: str) -> str | None:
        m = re.search(f'<meta[^>]*name="{field}"[^>]*content="([^"]*)"', html)
        return m.group(1) if m else None
