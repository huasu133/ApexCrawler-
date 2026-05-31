"""Font anti-crawl cracker: WOFF2/TTF parsing, OCR fallback, caching."""

from __future__ import annotations
import hashlib, logging, re, io
from typing import Optional

logger = logging.getLogger(__name__)

class FontCracker:
    """Cracks font-based anti-crawl protection."""
    
    def __init__(self, cache_backend=None):
        self._cache = cache_backend
    
    async def crack(self, html: str, page_url: str) -> dict[str, str]:
        """Main entry: detect font encoding, decode text."""
        font_urls = self._find_font_urls(html)
        if not font_urls:
            return {}
        
        result = {}
        for url in font_urls:
            mapping = await self._decode_font(url)
            result.update(mapping)
        return result
    
    def _find_font_urls(self, html: str) -> list[str]:
        """Find @font-face src URLs and base64 inline fonts."""
        urls = []
        # Standard @font-face
        urls.extend(re.findall(r"""@font-face\s*\{[^}]*?url\(["']?([^"')]+\.(?:woff2?|ttf|otf))""", html))
        # Base64 inline
        urls.extend(re.findall(r"""url\(data:font/[^;]+;base64,([^"')]+)""", html))
        return urls
    
    async def _decode_font(self, url: str) -> dict[str, str]:
        """Download and decode a font file. Returns char->glyph mapping."""
        cache_key = f"font:{hashlib.sha256(url.encode()).hexdigest()[:16]}"
        
        if self._cache:
            cached = await self._cache.get(cache_key)
            if cached:
                import pickle
                return pickle.loads(cached)
        
        # Try FontTools first
        try:
            mapping = await self._decode_with_fonttools(url)
        except Exception:
            logger.warning(f"FontTools failed for {url}, falling back to OCR")
            mapping = await self._decode_with_ocr(url)
        
        if self._cache and mapping:
            import pickle
            await self._cache.set(cache_key, pickle.dumps(mapping), ttl=86400)
        
        return mapping
    
    async def _decode_with_fonttools(self, url: str) -> dict[str, str]:
        """Parse font with FontTools (supports TTF, WOFF, WOFF2)."""
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            raise ImportError("fonttools[woff] required")
        
        # Download font
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=15)
            resp.raise_for_status()
            font_bytes = resp.content
        
        font = TTFont(io.BytesIO(font_bytes))
        cmap = font.getBestCmap()
        mapping = {}
        if cmap:
            for codepoint, glyph_name in cmap.items():
                char = chr(codepoint)
                mapping[glyph_name] = char
        font.close()
        return mapping
    
    async def _decode_with_ocr(self, url: str) -> dict[str, str]:
        """OCR-based fallback for AI-generated dynamic glyphs."""
        logger.info(f"Using OCR for font: {url}")
        try:
            import ddddocr
            ocr = ddddocr.DdddOcr(show_ad=False)
        except ImportError:
            logger.error("ddddocr not installed")
            return {}
        # OCR requires rendered glyph images — placeholder
        return {}
