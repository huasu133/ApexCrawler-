"""Self-healing XPath/CSS selectors via semantic matching + LLM fallback.

This module provides two layers of selector healing:
1. Legacy 'heal()' — regex-based fallback selectors for quick recovery.
2. Adaptive element tracking — DOM fingerprint + similarity scoring,
   inspired by the Scrapling library's adaptive relocation approach.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional

from lxml import html as lhtml
from lxml.etree import _Element
from lxml.html import HtmlElement

logger = logging.getLogger(__name__)


# ─── Adaptive Element Fingerprint ─────────────────────────────────────────────


@dataclass
class ElementFingerprint:
    """Structural fingerprint of a DOM element for adaptive re-location.

    Captures enough context about an element's position and identity in the
    DOM tree so that when the original selector fails (e.g. after a page
    structure change), the best-matching element can still be found.
    """

    tag: str
    classes: list[str] = field(default_factory=list)
    parent_path: str = ""        # Concatenated tag path from root → parent
    sibling_count: int = 0       # Number of same-level siblings
    text_hash: str = ""          # SHA-256 prefix of trimmed text content
    attributes: dict[str, str] = field(default_factory=dict)
    child_count: int = 0

    def __hash__(self) -> int:
        return hash((self.tag, self.text_hash, self.parent_path))


# ─── Existing Healer Classes (kept for backward compatibility) ────────────────


class SelHealer:
    """High-level selector self-healing for pipeline integration.

    Backward-compatible: the original ``heal()`` method still works exactly
    as before.  New adaptive tracking methods (``extract_fingerprint``,
    ``calculate_similarity``, ``track``) are added alongside.
    """

    def __init__(self):
        self._relocator = SemanticRelocator()

    # ── Legacy healing ────────────────────────────────────────────────────

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

    # ── Adaptive element tracking (new) ───────────────────────────────────

    @staticmethod
    def _get_element_path(element: HtmlElement) -> list[str]:
        """Build a list of tag names from document root down to *element*."""
        parts: list[str] = []
        cur: HtmlElement | None = element
        while cur is not None:
            parts.append(str(cur.tag))
            cur = cur.getparent()
        parts.reverse()
        return parts

    def extract_fingerprint(self, element: HtmlElement) -> ElementFingerprint:
        """Build an ``ElementFingerprint`` from an lxml ``HtmlElement``.

        This should be called *when the element is first successfully located*
        so the fingerprint can be stored and used later for re-location.
        """
        tag = str(element.tag)

        # Class list
        raw_class = element.get("class", "")
        classes = raw_class.split() if raw_class else []

        # Parent path (root → parent)
        parent = element.getparent()
        if parent is not None:
            path_parts = self._get_element_path(parent)
            parent_path = "/" + "/".join(path_parts) if path_parts else ""
        else:
            parent_path = ""

        # Sibling count
        if parent is not None:
            sibling_count = sum(
                1 for c in parent.iterchildren()
                if not isinstance(c, lhtml.HtmlComment) and c is not element
            )
        else:
            sibling_count = 0

        # Text hash
        text_content = (element.text_content() or "").strip()
        text_hash = hashlib.sha256(text_content.encode()).hexdigest()[:12] if text_content else ""

        # Attributes (excluding style which is often dynamic)
        attributes = {
            k: v for k, v in element.attrib.items()
            if k not in ("style",)
        }

        # Child count
        child_count = sum(
            1 for _ in element.iterchildren()
            if not isinstance(_, lhtml.HtmlComment)
        )

        return ElementFingerprint(
            tag=tag,
            classes=classes,
            parent_path=parent_path,
            sibling_count=sibling_count,
            text_hash=text_hash,
            attributes=attributes,
            child_count=child_count,
        )

    def calculate_similarity(
        self,
        fp1: ElementFingerprint,
        fp2: ElementFingerprint,
    ) -> float:
        """Compute a 0.0–1.0 similarity score between two fingerprints.

        Weighting (inspired by Scrapling's ``__calculate_similarity_score``):

        - **Tag match** — 40 %
        - **Class Jaccard similarity** — 20 %
        - **Parent path SequenceMatcher ratio** — 20 %
        - **Sibling count proximity** — 10 %
        - **Child count proximity** — 10 %
        """
        score = 0.0

        # 1. Tag match (40%)
        if fp1.tag == fp2.tag:
            score += 0.4

        # 2. Class Jaccard similarity (20%)
        if fp1.classes and fp2.classes:
            set1, set2 = set(fp1.classes), set(fp2.classes)
            intersection = len(set1 & set2)
            union = len(set1 | set2)
            score += 0.2 * (intersection / union if union > 0 else 0)
        elif not fp1.classes and not fp2.classes:
            # Both have no classes — neutral, give partial credit
            score += 0.1

        # 3. Parent path similarity (20%)
        score += 0.2 * SequenceMatcher(
            None, fp1.parent_path, fp2.parent_path
        ).ratio()

        # 4. Sibling count proximity (10%)
        max_sib = max(fp1.sibling_count, fp2.sibling_count) or 1
        sib_diff = abs(fp1.sibling_count - fp2.sibling_count)
        score += 0.1 * (1.0 - sib_diff / max_sib)

        # 5. Child count proximity (10%)
        max_child = max(fp1.child_count, fp2.child_count) or 1
        child_diff = abs(fp1.child_count - fp2.child_count)
        score += 0.1 * (1.0 - child_diff / max_child)

        return round(min(score, 1.0), 4)

    def _candidates_from_html(self, html: str) -> list[tuple[HtmlElement, ElementFingerprint]]:
        """Parse *html* and build ``(element, fingerprint)`` pairs for all non-root elements."""
        tree = lhtml.fromstring(html)
        candidates: list[tuple[HtmlElement, ElementFingerprint]] = []
        for elem in tree.iter():
            if isinstance(elem, lhtml.HtmlComment):
                continue
            if elem is tree:  # skip the synthetic <html> root itself
                continue
            fp = self.extract_fingerprint(elem)
            candidates.append((elem, fp))
        return candidates

    @staticmethod
    def _build_selector_from_element(
        element: HtmlElement,
        fp: ElementFingerprint | None = None,
    ) -> str:
        """Build a best-effort CSS selector for *element*.

        Prefers ``id`` > unique class composition > tag + parent path.
        """
        # If there is a unique id, it's the most reliable
        elem_id = element.get("id")
        if elem_id and not re.search(r"[\s:#>]", elem_id):
            return f"#{elem_id}"

        # Try tag + class chain
        if fp and fp.classes:
            class_sel = "".join(f".{re.escape(c)}" for c in fp.classes)
            return f"{fp.tag}{class_sel}"

        # Fall back to a simple tag selector
        return str(element.tag)

    def track(
        self,
        page,
        selector: str,
        fingerprint: ElementFingerprint | None = None,
    ) -> tuple[str, float]:
        """Locate an element on *page*, with adaptive fallback.

        **Workflow**

        1. Try the original *selector* against the page's current HTML.
        2. If it returns results, return ``(selector, 1.0)`` immediately.
        3. If no results AND *fingerprint* is provided, scan every DOM element,
           compute similarity scores against the fingerprint, and return the
           best match whose score exceeds the acceptance threshold.
        4. If nothing works, return ``(selector, 0.0)``.

        Parameters
        ----------
        page
            A Playwright ``Page`` object (must have a ``content()`` method
            that returns the full page HTML as a string).
        selector
            The original CSS / XPath selector to try first.
        fingerprint
            Optional ``ElementFingerprint`` captured when the element was
            first successfully located.  Used for adaptive re-location when
            the original selector fails.

        Returns
        -------
        tuple[str, float]
            ``(new_selector, confidence)`` where confidence is 0.0–1.0.
        """
        # -- Phase 1: try the original selector ----------------------------
        html = page.content()

        try:
            tree = lhtml.fromstring(html)
            if selector.startswith("//"):
                results = tree.xpath(selector)
            else:
                results = tree.cssselect(selector)

            if results:
                logger.debug("track: original selector still works")
                return (selector, 1.0)
        except Exception:
            logger.debug("track: original selector raised an error, will try adaptive")

        # -- Phase 2: adaptive re-location via fingerprint ------------------
        if fingerprint is None:
            logger.debug("track: no fingerprint provided, cannot recover")
            return (selector, 0.0)

        candidates = self._candidates_from_html(html)
        if not candidates:
            return (selector, 0.0)

        scored: list[tuple[float, HtmlElement, ElementFingerprint]] = []
        for elem, cand_fp in candidates:
            sim = self.calculate_similarity(fingerprint, cand_fp)
            scored.append((sim, elem, cand_fp))

        # Sort descending by similarity
        scored.sort(key=lambda x: x[0], reverse=True)

        best_score, best_elem, best_fp = scored[0]

        # Acceptance threshold — require at least 60 % similarity
        ACCEPT_THRESHOLD = 0.60

        if best_score >= ACCEPT_THRESHOLD:
            new_selector = self._build_selector_from_element(best_elem, best_fp)
            logger.debug(
                "track: adaptive re-location found element "
                "with score %.2f -> %s",
                best_score,
                new_selector,
            )
            return (new_selector, best_score)

        logger.debug(
            "track: best candidate scored %.2f (below %.2f threshold)",
            best_score,
            ACCEPT_THRESHOLD,
        )
        return (selector, 0.0)


# ─── SemanticRelocator (kept for backward compatibility) ──────────────────────


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
        except Exception as e:
            logger.debug(f"selector test failed: {e}")
            return False
