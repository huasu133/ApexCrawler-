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
                try:
                    return pickle.loads(cached)
                except Exception:
                    logger.warning(f"Failed to unpickle font cache for {url}, re-decoding")
        
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
        """OCR-based fallback: render glyphs → OCR → build char mapping."""
        try:
            import httpx
            from fontTools.ttLib import TTFont
            from PIL import Image, ImageDraw, ImageFont

            # Download font
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
                r = await c.get(url)
                r.raise_for_status()
                font_data = io.BytesIO(r.content)

            font = TTFont(font_data)
            cmap = font.getBestCmap() or {}
            glyph_map: dict[str, str] = {}

            # Render each glyph and OCR it
            from .ocr_engine import OCREngine
            ocr = OCREngine(backend="paddleocr")

            font_size = 48
            font_data.seek(0)  # reset after TTFont read
            pil_font = ImageFont.truetype(font_data, font_size)

            for codepoint, glyph_name in list(cmap.items())[:500]:  # limit to 500 chars
                try:
                    char = chr(codepoint)
                    img = Image.new('L', (64, 64), 255)
                    draw = ImageDraw.Draw(img)
                    draw.text((8, 8), char, font=pil_font, fill=0)

                    img_bytes = io.BytesIO()
                    img.save(img_bytes, format='PNG')

                    result = await ocr.recognize(img_bytes.getvalue())
                    if result and result.confidence > 0.6:
                        glyph_map[char] = result.text.strip()
                except Exception:
                    continue

            font.close()
            if glyph_map:
                logger.info(f"OCR decoded {len(glyph_map)} glyphs from {url}")
            return glyph_map
        except ImportError:
            logger.warning("PIL/fontTools not available for OCR fallback")
            return {}
        except Exception as e:
            logger.warning(f"OCR decoding failed: {e}")
            return {}
