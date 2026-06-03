"""Font anti-crawl recovery chain: FontTools → ddddocr → PaddleOCR → contour matching."""

import logging

logger = logging.getLogger(__name__)


class FontRecoveryManager:
    """Multi-level font decoding fallback chain."""

    STRATEGIES = ["fonttools", "ddddocr", "paddleocr", "contour_match"]

    def __init__(self):
        self._fallback_order = ["fonttools", "paddleocr", "ddddocr"]

    async def decode(self, font_url: str, html: str) -> dict[str, str]:
        """Try each strategy in order until one succeeds."""
        errors = []
        for strategy in self._fallback_order:
            try:
                result = await self._try_strategy(strategy, font_url, html)
                if result:
                    logger.info(f"Font decoded via {strategy}: {len(result)} glyphs")
                    return result
            except Exception as e:
                errors.append(f"{strategy}: {e}")
        logger.error(f"All font strategies failed: {', '.join(errors)}")
        return {}

    async def _try_strategy(self, strategy: str, font_url: str, html: str) -> dict:
        if strategy == "fonttools":
            return await self._decode_with_fonttools(font_url)
        elif strategy == "ddddocr":
            return await self._decode_with_ddddocr(font_url)
        elif strategy == "paddleocr":
            return await self._decode_with_paddleocr(font_url)
        return {}

    async def _decode_with_fonttools(self, url: str) -> dict:
        import io

        import httpx
        from fontTools.ttLib import TTFont

        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url)
            r.raise_for_status()
            font = TTFont(io.BytesIO(r.content))
            cmap = font.getBestCmap()
            mapping = {}
            if cmap:
                for codepoint, glyph_name in cmap.items():
                    mapping[glyph_name] = chr(codepoint)
            font.close()
            return mapping

    async def _decode_with_ddddocr(self, url: str) -> dict:
        from .font_cracker import FontCracker

        fc = FontCracker()
        return await fc._decode_with_ocr(url)

    async def _decode_with_paddleocr(self, url: str) -> dict:
        from .font_cracker import FontCracker

        fc = FontCracker()
        return await fc._decode_with_ocr(url)
