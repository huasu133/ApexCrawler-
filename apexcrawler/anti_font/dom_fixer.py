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

    _PSEUDO_RULE_FULL_RE = re.compile(
        r'([^{}]+::?(?:before|after))\s*\{([^}]*)\}',
        re.IGNORECASE | re.DOTALL,
    )
    _PSEUDO_STRIP_RE = re.compile(r'::?(?:before|after)\s*$', re.IGNORECASE)
    _CONTENT_PROP_RE = re.compile(r'content\s*:\s*["\']([^"\']*)["\']', re.IGNORECASE)

    def _inject_pseudo_content(self, html: str) -> str:
        """Extract content from CSS ::before/::after rules and inject near target elements.

        Each pseudo-element's content is injected as a hidden span right after
        the matching selector's element, instead of all at the </body> marker.
        """
        css_blocks = re.findall(r"<style[^>]*>(.*?)</style>", html, re.DOTALL | re.IGNORECASE)
        if not css_blocks:
            return html

        injections: list[tuple[str, str]] = []  # (plain_selector, content_text)
        for block in css_blocks:
            for rule in self._PSEUDO_RULE_FULL_RE.finditer(block):
                full_selector = rule.group(1).strip()
                body = rule.group(2)
                content_match = self._CONTENT_PROP_RE.search(body)
                if not content_match:
                    continue
                text = content_match.group(1) or content_match.group(2) or ""
                if not text:
                    continue

                # Strip ::before/::after to get plain selector
                plain_selector = self._PSEUDO_STRIP_RE.sub("", full_selector).strip()
                if not plain_selector:
                    continue

                injections.append((plain_selector, text))

        if not injections:
            return html

        seen: set[tuple[str, str]] = set()
        for selector, text in injections:
            if (selector, text) in seen:
                continue
            seen.add((selector, text))

            marker = f'<span data-apex-pseudo-content="{text}" style="display:none">{text}</span>'

            # Simple: match by class or tag selector
            # Handle .class selectors
            if selector.startswith("."):
                class_name = selector[1:].split(":")[0].strip()
                if not class_name:
                    continue
                # Find elements with this class and inject after the last one
                class_pat = re.compile(
                    rf'(<[^>]*class\s*=\s*"[^"]*\b{class_name}\b[^"]*"[^>]*>)(.*?</[^>]+>)',
                    re.DOTALL | re.IGNORECASE,
                )
                matches = list(class_pat.finditer(html))
                if matches:
                    # Inject after each matching opening tag
                    result_parts = []
                    last_end = 0
                    for m in matches:
                        result_parts.append(html[last_end:m.end(1)])
                        result_parts.append(marker)
                        last_end = m.end(1)
                    result_parts.append(html[last_end:])
                    html = "".join(result_parts)
            elif selector.startswith("#"):
                # ID selector: inject after element with matching id
                id_name = selector[1:].split(":")[0].strip()
                if not id_name:
                    continue
                id_pat = re.compile(
                    rf'(<[^>]*\bid\s*=\s*["\']{id_name}["\'][^>]*>)(.*?</[^>]+>)',
                    re.DOTALL | re.IGNORECASE,
                )
                matches = list(id_pat.finditer(html))
                if matches:
                    result_parts = []
                    last_end = 0
                    for m in matches:
                        result_parts.append(html[last_end:m.end(1)])
                        result_parts.append(marker)
                        last_end = m.end(1)
                    result_parts.append(html[last_end:])
                    html = "".join(result_parts)
            elif re.match(r'^[a-zA-Z][a-zA-Z0-9]*$', selector):
                # Tag name selector: inject after each matching opening tag
                tag_pat = re.compile(
                    rf'(<{selector}\b[^>]*>)',
                    re.IGNORECASE,
                )
                if tag_pat.search(html):
                    html = tag_pat.sub(rf'\1{marker}', html)

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
