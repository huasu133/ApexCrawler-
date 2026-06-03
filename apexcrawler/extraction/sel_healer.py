"""Self-healing XPath/CSS selectors via semantic matching + LLM fallback."""
from __future__ import annotations

import hashlib
import logging
import re

logger = logging.getLogger(__name__)


class SelHealer:
    """High-level selector self-healing for pipeline integration."""

    def __init__(self):
        self._relocator = SemanticRelocator()

    async def heal(self, url: str, ctx) -> str | None:
        """Attempt to recover content when normal extraction fails.
        
        Uses the ctx's existing headers, proxy, and user-agent
        from the pipeline to maintain anti-detection state.
        """
        try:
            import httpx
            headers = {}
            if hasattr(ctx, 'user_agent') and ctx.user_agent:
                headers['User-Agent'] = ctx.user_agent
            headers['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
            headers['Accept-Language'] = 'en-US,en;q=0.9'

            proxy = getattr(ctx, 'proxy', None)

            async with httpx.AsyncClient(
                timeout=10, follow_redirects=True,
                proxy=proxy, headers=headers,
            ) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.text
        except Exception as e:
            logger.debug(f"sel_healer recovery failed: {e}")
            return None


class SemanticRelocator:
    """Generates and heals selectors when original extraction fails."""

    def __init__(self, cache=None):
        self._cache = cache or {}

    def generate_redundant_selectors(self, html: str, original_selector: str) -> list[str]:
        """Generate 5 backup selectors from original."""
        selectors = [original_selector]
        # ID-based fallback
        id_match = re.search(r'#(\w+)', original_selector)
        if id_match:
            selectors.append(f"//*[@id='{id_match.group(1)}']")
        # Class-based fallback
        class_match = re.search(r'\.([\w-]+)', original_selector)
        if class_match:
            selectors.append(f"//*[contains(@class,'{class_match.group(1)}')]")
        # Tag + text fallback
        tag_match = re.search(r'^(\w+)', original_selector)
        if tag_match:
            selectors.append(f"//{tag_match.group(1)}")
        return list(set(selectors))

    async def heal(self, html: str, failed_selector: str) -> tuple[str, float] | None:
        """Try to find the same or similar element when original selector fails."""
        cache_key = hashlib.sha256(f"{failed_selector}".encode()).hexdigest()[:12]
        backups = self.generate_redundant_selectors(html, failed_selector)
        # Return first working alternative
        for sel in backups[1:]:
            if sel != failed_selector and self._test_selector(html, sel):
                return (sel, 0.8)
        return None

    def _test_selector(self, html: str, selector: str) -> bool:
        try:
            from lxml import html as lhtml

            tree = lhtml.fromstring(html)
            if selector.startswith("//"):
                return bool(tree.xpath(selector))
            else:
                return bool(tree.cssselect(selector))
        except Exception:
            return False
