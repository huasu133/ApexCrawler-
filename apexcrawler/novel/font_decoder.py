"""
Universal font anti-captcha decoder for novel sites.
Handles: Qidian (数字), Fanqie (汉字全加密), Zongheng, etc.

Three-level fallback:
1. fontTools cmap + standard mapping
2. fontTools + PIL render + ddddocr OCR
3. Return original text as-is
"""
from __future__ import annotations
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class FontDecoder:
    """Download and decode custom fonts used for anti-captcha on novel sites."""

    def __init__(self, cache_dir: str | None = None):
        base = Path(cache_dir) if cache_dir else Path.home() / ".apexcrawler" / "fonts"
        base.mkdir(parents=True, exist_ok=True)
        self._cache_dir = base
        self._mapping_cache: Dict[str, Dict[str, str]] = {}
        self._ocr = None

    def decode_html(self, html: str, font_url: str | None = None) -> str:
        """Decode font-encrypted text in HTML.

        Attempts:
        1. Font URL extraction from @font-face
        2. cmap standard mapping
        3. OCR rendering fallback
        """
        if not font_url:
            font_url = self._extract_font_url(html)

        if not font_url:
            logger.debug("No custom font found in HTML")
            return html

        mapping = self._get_mapping(font_url)
        if not mapping:
            return html

        # Replace obfuscated characters in the HTML
        for original, decoded in mapping.items():
            html = html.replace(original, decoded)

        return html

    def _extract_font_url(self, html: str) -> str | None:
        """Extract custom font URL from @font-face CSS in HTML."""
        patterns = [
            r'@font-face\s*\{[^}]*src:\s*url\(["\']?([^"\'\)]+\.(?:woff2?|ttf))',
            r'@font-face[^}]*src:[^}]*url\(["\']?([^"\'\)]+)',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
            if m:
                url = m.group(1)
                if url.startswith("//"):
                    url = "https:" + url
                return url
        return None

    def _get_mapping(self, font_url: str) -> Dict[str, str]:
        """Get or create character mapping for a font URL."""
        if font_url in self._mapping_cache:
            return self._mapping_cache[font_url]

        font_path = self._download_font(font_url)
        if not font_path:
            return {}

        mapping = self._build_mapping(font_path)
        self._mapping_cache[font_url] = mapping
        return mapping

    def _download_font(self, url: str) -> str | None:
        """Download font file to cache."""
        url_hash = hashlib.md5(url.encode()).hexdigest()[:16]
        cached = self._cache_dir / f"{url_hash}.woff"

        if cached.exists():
            logger.debug("Font cache hit: %s", cached)
            return str(cached)

        try:
            import httpx
            r = httpx.get(url, follow_redirects=True, timeout=15, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0",
            })
            if r.status_code == 200:
                cached.write_bytes(r.content)
                logger.debug("Font downloaded: %s (%d bytes)", url, len(r.content))
                return str(cached)
        except Exception as e:
            logger.warning("Font download failed: %s", e)

        return None

    def _build_mapping(self, font_path: str) -> Dict[str, str]:
        """Build character mapping from font file.

        Level 1: Try fontTools cmap + common encoding patterns (数字映射)
        Level 2: Fall back to OCR (ddddocr) for each glyph
        """
        mapping: Dict[str, str] = {}

        try:
            from fonttools.ttLib import TTFont
            font = TTFont(font_path)
            cmap = font.getBestCmap()

            # Level 1: Try standard Unicode mapping
            for char_code, glyph_name in cmap.items():
                char = chr(char_code)
                # Check if this looks like a standard Unicode character
                if 0x4E00 <= char_code <= 0x9FFF:  # CJK
                    mapping[char] = char
                elif 0x30 <= char_code <= 0x39:  # 0-9
                    mapping[char] = char
                elif 0x41 <= char_code <= 0x5A or 0x61 <= char_code <= 0x7A:  # A-Z a-z
                    mapping[char] = char

            # Level 2: OCR-based decoding for custom glyphs
            # Render each glyph and OCR it
            try:
                from PIL import Image, ImageDraw, ImageFont
                if self._ocr is None:
                    import ddddocr
                    self._ocr = ddddocr.DdddOcr()

                # Find glyph outlines
                glyf_table = font.get("glyf")
                if glyf_table:
                    pil_font = ImageFont.truetype(font_path, 48)
                    for char_code, glyph_name in cmap.items():
                        if char_code < 0x20:
                            continue
                        try:
                            glyph = glyf_table[glyph_name]
                            if glyph.numberOfContours == 0:
                                continue

                            # Render glyph to image
                            char = chr(char_code)
                            img = Image.new("L", (64, 64), 255)
                            draw = ImageDraw.Draw(img)
                            bbox = draw.textbbox((0, 0), char, font=pil_font)
                            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                            if w < 4 or h < 4:
                                continue

                            img2 = Image.new("L", (w + 8, h + 8), 255)
                            draw2 = ImageDraw.Draw(img2)
                            draw2.text((4, 4), char, font=pil_font, fill=0)

                            result = self._ocr.classification(img2)
                            if result and result != char and len(result) == 1:
                                mapping[char] = result
                        except Exception:
                            continue
            except ImportError:
                logger.debug("OCR dependencies (PIL/ddddocr) not available, skipping")

            font.close()
        except Exception as e:
            logger.warning("Font processing failed: %s", e)

        return mapping


# Singleton for reuse
_default_decoder: FontDecoder | None = None


def get_decoder() -> FontDecoder:
    global _default_decoder
    if _default_decoder is None:
        _default_decoder = FontDecoder()
    return _default_decoder


def decode(html: str, font_url: str | None = None) -> str:
    """Convenience function: decode font-encrypted HTML in one call."""
    return get_decoder().decode_html(html, font_url)
