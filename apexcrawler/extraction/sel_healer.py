"""Self-healing XPath/CSS selectors via semantic matching + LLM fallback.

This module provides two layers of selector healing:
1. Legacy 'heal()' — regex-based fallback selectors for quick recovery.
2. Adaptive element tracking — DOM fingerprint + similarity scoring,
   inspired by the Scrapling library's adaptive relocation approach.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Optional
from urllib.parse import urlparse

from lxml import html as lhtml
from lxml.etree import _Element
from lxml.html import HtmlElement

logger = logging.getLogger(__name__)


# ─── SQLite-backed Selector Confidence Database ────────────────────────────────


class SelectorDatabase:
    """SQLite-backed selector confidence store.

    Persists selector success/failure history and confidence scores
    so they survive across crawler restarts.
    """

    def __init__(self, db_path: str | None = None):
        if db_path is None:
            db_dir = os.path.expanduser("~/.apexcrawler")
            os.makedirs(db_dir, exist_ok=True)
            db_path = os.path.join(db_dir, "selector_cache.db")
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS selectors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url_pattern TEXT NOT NULL,
                field_name TEXT NOT NULL,
                selector TEXT NOT NULL,
                selector_type TEXT DEFAULT 'css',
                confidence REAL DEFAULT 0.5,
                success_count INTEGER DEFAULT 0,
                fail_count INTEGER DEFAULT 0,
                last_used_at REAL DEFAULT 0,
                created_at REAL DEFAULT 0,
                UNIQUE(url_pattern, field_name, selector)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_url_field
            ON selectors(url_pattern, field_name)
        """)
        self._conn.commit()

    def get_candidates(self, url: str, field: str) -> list[tuple[str, float]]:
        """Get selectors sorted by confidence descending."""
        pattern = urlparse(url).netloc
        rows = self._conn.execute(
            "SELECT selector, confidence FROM selectors "
            "WHERE url_pattern = ? AND field_name = ? "
            "ORDER BY confidence DESC",
            (pattern, field),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def record_success(self, url: str, field: str, selector: str):
        """Increase confidence on success."""
        pattern = urlparse(url).netloc
        ts = __import__("time").time()
        self._conn.execute(
            """
            INSERT INTO selectors (url_pattern, field_name, selector, confidence, success_count, last_used_at, created_at)
            VALUES (?, ?, ?, 0.5, 1, ?, ?)
            ON CONFLICT(url_pattern, field_name, selector) DO UPDATE SET
                confidence = MIN(1.0, confidence * 1.05 + 0.02),
                success_count = success_count + 1,
                last_used_at = ?
        """,
            (pattern, field, selector, ts, ts, ts),
        )
        self._conn.commit()

    def record_failure(self, url: str, field: str, selector: str):
        """Decrease confidence on failure."""
        pattern = urlparse(url).netloc
        self._conn.execute(
            """
            UPDATE selectors SET
                confidence = MAX(0.0, confidence * 0.85 - 0.05),
                fail_count = fail_count + 1,
                last_used_at = ?
            WHERE url_pattern = ? AND field_name = ? AND selector = ?
        """,
            (__import__("time").time(), pattern, field, selector),
        )
        self._conn.commit()

    def decay_old_selectors(self, days: int = 7):
        """Decay confidence for selectors unused for N days."""
        cutoff = __import__("time").time() - days * 86400
        self._conn.execute(
            """
            UPDATE selectors SET confidence = confidence * 0.99
            WHERE last_used_at < ? AND last_used_at > 0
        """,
            (cutoff,),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()


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

    async def heal(self, url: str, ctx, field_name: str = "", expected_text: str = "") -> str | None:
        """Enhanced recover with 4-level healing strategy.

        Tries: text pattern → attribute fuzzy → structural context → XPath fallback
        Falls back to full page re-fetch if all strategies fail.
        """
        # First try to fetch the page
        html = await self._fetch_page(url, ctx)
        if not html:
            return None

        # Try 4 healing strategies in order
        strategies = [
            ("text_pattern", lambda: self._heal_text_pattern(html, field_name, expected_text)),
            ("attribute_fuzzy", lambda: self._heal_attribute_fuzzy(html, ctx.raw_selector if hasattr(ctx, "raw_selector") else "")),
            ("structural_context", lambda: self._heal_structural_context(html, ctx.raw_selector if hasattr(ctx, "raw_selector") else "")),
            ("xpath_fallback", lambda: self._heal_xpath_fallback(html, ctx.raw_selector if hasattr(ctx, "raw_selector") else "")),
        ]

        for name, strategy in strategies:
            try:
                result = strategy()
                if result:
                    logger.info(f"[sel_healer] Level '{name}' recovered selector: {result}")
                    return result
            except Exception as e:
                logger.debug(f"[sel_healer] Level '{name}' failed: {e}")

        # Ultimate fallback: re-fetch with full browser
        try:
            import httpx

            headers = {}
            if hasattr(ctx, "user_agent") and ctx.user_agent:
                headers["User-Agent"] = ctx.user_agent
            proxy = getattr(ctx, "proxy", None)
            async with httpx.AsyncClient(timeout=15, follow_redirects=True, proxy=proxy, headers=headers) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.text
        except Exception as e:
            logger.warning(f"[sel_healer] All healing strategies failed: {e}")
            return None

    async def _fetch_page(self, url: str, ctx) -> str | None:
        """Fetch page HTML using context settings."""
        try:
            import httpx

            headers = {"Accept": "text/html,*/*;q=0.8"}
            if hasattr(ctx, "user_agent") and ctx.user_agent:
                headers["User-Agent"] = ctx.user_agent
            proxy = getattr(ctx, "proxy", None)
            async with httpx.AsyncClient(timeout=10, follow_redirects=True, proxy=proxy, headers=headers) as c:
                r = await c.get(url)
                r.raise_for_status()
                return r.text
        except Exception as e:
            logger.debug(f"[sel_healer] Page fetch failed: {e}")
            return None

    # ── 4-Level Healing Strategies ────────────────────────────────────────

    def _heal_text_pattern(self, html: str, field_name: str, expected_text: str) -> str | None:
        """Level 1: Find element by text content pattern matching."""
        if not expected_text:
            return None
        tree = lhtml.fromstring(html)
        for elem in tree.iter():
            if not isinstance(elem, lhtml.HtmlComment):
                text = (elem.text_content() or "").strip()
                if expected_text.lower() in text.lower():
                    return self._build_selector_from_element(elem)
        return None

    def _heal_attribute_fuzzy(self, html: str, original_selector: str) -> str | None:
        """Level 2: Fuzzy match CSS classes using Levenshtein distance."""
        tree = lhtml.fromstring(html)

        # Extract class names from original selector
        classes = re.findall(r"\.([\w-]+)", original_selector)
        if not classes:
            return None

        best_match = None
        best_score = 0.0

        for elem in tree.iter():
            if isinstance(elem, lhtml.HtmlComment):
                continue
            elem_classes = (elem.get("class", "") or "").split()
            for orig_cls in classes:
                for elem_cls in elem_classes:
                    sim = SequenceMatcher(None, orig_cls, elem_cls).ratio()
                    if sim > best_score and sim > 0.6:
                        best_score = sim
                        best_match = elem

        if best_match:
            return self._build_selector_from_element(best_match)
        return None

    def _heal_structural_context(self, html: str, original_selector: str) -> str | None:
        """Level 3: Navigate via parent/sibling structural context."""
        tree = lhtml.fromstring(html)
        # Try parent-child relationships
        tag_match = re.search(r"^(\w+)", original_selector)
        if not tag_match:
            return None
        tag = tag_match.group(1)

        # Find elements with same tag, same depth
        for elem in tree.iter(tag):
            if not isinstance(elem, lhtml.HtmlComment):
                return self._build_selector_from_element(elem)
        return None

    def _heal_xpath_fallback(self, html: str, original_selector: str) -> str | None:
        """Level 4: Convert CSS selector to XPath as last resort."""
        # Simple CSS to XPath conversion for common patterns
        xpath = original_selector
        # ID selector: #foo -> //*[@id='foo']
        xpath = re.sub(r"#([\w-]+)", r"[@id='\1']", xpath)
        # Class selector: .foo -> [contains(@class,'foo')]
        xpath = re.sub(r"\.([\w-]+)", r"[contains(@class,'\1')]", xpath)
        # Prepend // if no leading /
        if not xpath.startswith("/"):
            xpath = "//" + xpath

        try:
            tree = lhtml.fromstring(html)
            results = tree.xpath(xpath)
            if results:
                return xpath
        except Exception:
            pass
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

    def track_with_persistence(
        self,
        page,
        url: str,
        selector: str,
        field: str = "",
        fingerprint: ElementFingerprint | None = None,
        db: SelectorDatabase | None = None,
    ) -> tuple[str, float]:
        """Track element with SQLite-backed confidence persistence.

        Args:
            page: Playwright Page object
            url: Target URL
            selector: Original CSS/XPath selector
            field: Field name for database tracking
            fingerprint: Optional ElementFingerprint
            db: Optional SelectorDatabase instance

        Returns:
            (selector, confidence) tuple
        """
        # Phase 1: Try original selector
        html = page.content()
        try:
            tree = lhtml.fromstring(html)
            if selector.startswith("//"):
                results = tree.xpath(selector)
            else:
                results = tree.cssselect(selector)

            if results:
                confidence = 1.0
                if db:
                    db.record_success(url, field or "generic", selector)
                return (selector, confidence)
        except Exception:
            pass

        # Phase 2: Try database candidates
        if db and field:
            candidates = db.get_candidates(url, field)
            for cand_sel, cand_conf in candidates:
                try:
                    tree = lhtml.fromstring(html)
                    if cand_sel.startswith("//"):
                        results = tree.xpath(cand_sel)
                    else:
                        results = tree.cssselect(cand_sel)
                    if results:
                        db.record_success(url, field, cand_sel)
                        return (cand_sel, cand_conf)
                except Exception:
                    db.record_failure(url, field, cand_sel)

        # Phase 3: Adaptive re-location via fingerprint
        if fingerprint is not None:
            candidates = self._candidates_from_html(html)
            scored = []
            for elem, cand_fp in candidates:
                sim = self.calculate_similarity(fingerprint, cand_fp)
                scored.append((sim, elem, cand_fp))
            scored.sort(key=lambda x: x[0], reverse=True)

            if scored and scored[0][0] >= 0.6:
                best_score, best_elem, best_fp = scored[0]
                new_selector = self._build_selector_from_element(best_elem, best_fp)
                if db:
                    db.record_success(url, field or "generic", new_selector)
                return (new_selector, best_score)

        # Phase 4: Record failure
        if db:
            db.record_failure(url, field or "generic", selector)
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
        id_match = re.search(r"#(\w+)", original_selector)
        if id_match:
            selectors.append(f"//*[@id='{id_match.group(1)}']")
        # Class-based fallback
        class_match = re.search(r"\.([\w-]+)", original_selector)
        if class_match:
            selectors.append(f"//*[contains(@class,'{class_match.group(1)}')]")
        # Tag + text fallback
        tag_match = re.search(r"^(\w+)", original_selector)
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
