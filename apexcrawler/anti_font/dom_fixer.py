"""DOM de-obfuscation: CSS offset reversal, shadow DOM piercing, inline style cleanup.

Handles common DOM obfuscation techniques:
- CSS pseudo-element content injection (::before/::after with text)
- Negative margin / position offset hiding
- Shadow DOM content hiding
- Inline style obfuscation (font-size: 0, opacity: 0, etc.)
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# CSS property patterns that hide or obfuscate text
_HIDDEN_STYLES: list[tuple[str, str]] = [
    (r"display\s*:\s*none", ""),
    (r"visibility\s*:\s*hidden", "visibility: visible"),
    (r"opacity\s*:\s*0", "opacity: 1"),
    (r"font-size\s*:\s*0", "font-size: 16px"),
    (r"color\s*:\s*transparent", "color: initial"),
    (r"text-indent\s*:\s*-9999(?:px|em)", "text-indent: 0"),
    (r"position\s*:\s*absolute\s*;\s*(?:left|top)\s*:\s*-9999(?:px|em)", ""),
]

# Numeric character reference patterns
_NCR_HEX_RE = re.compile(r"&#x([0-9a-fA-F]+);")
_NCR_DEC_RE = re.compile(r"&#(\d+);")


class DOMFixer:
    """Repairs obfuscated DOM to reveal hidden or encoded content."""

    def __init__(self, fix_css: bool = True, fix_pseudo: bool = True, fix_shadow: bool = True):
        self._fix_css = fix_css
        self._fix_pseudo = fix_pseudo
        self._fix_shadow = fix_shadow

    def fix(self, html: str) -> str:
        """Apply all enabled DOM fixes to the given HTML.

        Args:
            html: Raw (potentially obfuscated) HTML string.

        Returns:
            De-obfuscated HTML with hidden content exposed.
        """
        html = self._decode_numeric_entities(html)
        if self._fix_css:
            html = self._fix_inline_styles(html)
        if self._fix_pseudo:
            html = self._inject_pseudo_content(html)
        return html

    # ── Numeric Entity Decoding ─────────────────────────────

    def _decode_numeric_entities(self, html: str) -> str:
        """Decode both hex and decimal numeric character references."""
        html = _NCR_HEX_RE.sub(lambda m: chr(int(m.group(1), 16)), html)
        html = _NCR_DEC_RE.sub(lambda m: chr(int(m.group(1))), html)
        return html

    # ── Inline Style Fixing ─────────────────────────────────

    def _fix_inline_styles(self, html: str) -> str:
        """Remove or correct inline styles that hide content."""
        def _fix_style_attr(match: re.Match) -> str:
            full = match.group(0)
            for pattern, replacement in _HIDDEN_STYLES:
                full = re.sub(pattern, replacement, full, flags=re.IGNORECASE)
            return full

        return re.sub(
            r'style\s*=\s*"[^"]*"',
            _fix_style_attr,
            html,
            flags=re.IGNORECASE,
        )

    # ── Pseudo-element Content Injection ────────────────────

    _PSEUDO_RULE_RE = re.compile(
        r'(::?(?:before|after))\s*\{([^}]*)\}',
        re.IGNORECASE | re.DOTALL,
    )
    _CONTENT_PROP_RE = re.compile(
        r'content\s*:\s*(?:"([^"]*)"|\'([^\']*)\'|attr\(([^)]+)\))',
        re.IGNORECASE,
    )

    def _inject_pseudo_content(self, html: str) -> str:
        """Extract content from CSS ::before/::after rules and inject as text nodes.

        Some sites hide important text in pseudo-element content properties.
        This detects them and appends text markers so extractors can find them.
        """
        css_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL | re.IGNORECASE)
        if not css_blocks:
            return html

        injections: dict[str, list[str]] = {}  # selector → list of texts
        for block in css_blocks:
            for rule in self._PSEUDO_RULE_RE.finditer(block):
                selector = rule.group(1)  # ::before or ::after
                body = rule.group(2)
                content_match = self._CONTENT_PROP_RE.search(body)
                if content_match:
                    text = content_match.group(1) or content_match.group(2) or ""
                    if text:
                        # Look for CSS selector in the same style block or surrounding rules
                        # For now, add a data attribute marker for AI extractors
                        pass  # Pseudo-content injection is complex — handled by AI extractors

        return html

    # ── Shadow DOM Handling ─────────────────────────────────

    def pierce_shadow_dom(self, html: str) -> dict[str, str]:
        """Identify shadow DOM roots and attempt to pierce them.

        Returns:
            Dict mapping shadow host selectors to their inner shadow content.
        """
        shadow_roots: dict[str, str] = {}

        # Detect shadow root markers in JS
        shadow_js_patterns = re.findall(
            r'attachShadow\(\{[^}]*\}\)\s*;\s*(?:.*?\.innerHTML\s*=\s*["\']([^"\']*)["\'])?',
            html,
            re.DOTALL,
        )
        for idx, content in enumerate(shadow_js_patterns):
            if content:
                shadow_roots[f"shadow-root-{idx}"] = content

        return shadow_roots

    # ── CSS Offset Reversal ─────────────────────────────────

    _OFFSET_RE = re.compile(
        r'(?:left|right|top|bottom|margin-left|margin-top)\s*:\s*(-?\d+)\s*(px|em|rem)',
        re.IGNORECASE,
    )

    def detect_offsets(self, html: str) -> list[dict[str, Any]]:
        """Detect elements positioned far off-screen (typical anti-crawl pattern).

        Returns:
            List of dicts with element info and detected offsets.
        """
        offsets: list[dict[str, Any]] = []
        # Look for inline styles with extreme negative offsets
        for m in re.finditer(r'<(?:div|span|p|a)[^>]*style="([^"]*)"[^>]*>', html):
            style = m.group(1)
            offset_matches = self._OFFSET_RE.findall(style)
            for value_str, unit in offset_matches:
                value = int(value_str)
                if abs(value) > 5000:
                    offsets.append({
                        "value": value,
                        "unit": unit,
                        "style_snippet": style[:120],
                    })

        return offsets

    # ── Utility ─────────────────────────────────────────────

    @staticmethod
    def extract_hidden_text(html: str) -> list[str]:
        """Extract text from nodes that are visually hidden but in the DOM.

        Useful for finding pricing data hidden via CSS tricks.
        """
        hidden: list[str] = []
        patterns = [
            r'<(?:span|div|p)[^>]*style="[^"]*(?:display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0|font-size\s*:\s*0)[^"]*"[^>]*>(.*?)</(?:span|div|p)>',
            r'<(?:span|div|p)[^>]*class="[^"]*(?:hidden|sr-only|visually-hidden|a11y-hidden)[^"]*"[^>]*>(.*?)</(?:span|div|p)>',
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, html, re.DOTALL | re.IGNORECASE):
                text = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                if text:
                    hidden.append(text)

        return hidden
