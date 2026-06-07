"""
Content filtering using pruning algorithm with dynamic threshold.
Filters HTML content to keep only meaningful text blocks.
Inspiration: Crawl4AI's PruningContentFilter.
"""
import math
import re
from typing import Optional
from bs4 import BeautifulSoup, Comment


# Tags to exclude entirely from content
EXCLUDED_TAGS = {
    "nav", "footer", "header", "aside",
    "script", "style", "form", "iframe", "noscript",
}

# Tag importance multipliers for dynamic threshold adjustment
TAG_IMPORTANCE = {
    "article": 1.5,
    "main": 1.4,
    "section": 1.3,
    "p": 1.2,
    "h1": 1.4,
    "h2": 1.3,
    "h3": 1.2,
    "div": 0.7,
    "span": 0.6,
}

# Tag weights for composite scoring
TAG_WEIGHTS = {
    "div": 0.5,
    "p": 1.0,
    "article": 1.5,
    "section": 1.0,
    "span": 0.3,
    "li": 0.5,
    "ul": 0.5,
    "ol": 0.5,
    "h1": 1.2,
    "h2": 1.1,
    "h3": 1.0,
    "h4": 0.9,
    "h5": 0.8,
    "h6": 0.7,
}

# Weights for each scoring metric
METRIC_WEIGHTS = {
    "text_density": 0.4,
    "link_density": 0.2,
    "tag_weight": 0.2,
    "class_id_weight": 0.1,
    "text_length": 0.1,
}

# Patterns indicating non-content elements
NEGATIVE_PATTERNS = re.compile(
    r"nav|footer|header|sidebar|ads|comment|promo|advert|social|share", re.I
)


class PruningContentFilter:
    """
    Filters HTML content by scoring each text chunk.

    Algorithm:
    1. Parse HTML with BeautifulSoup
    2. Remove comments and unwanted tags
    3. Recursively prune tree, scoring each node
    4. Keep only nodes above a dynamic threshold
    5. Return remaining content as HTML strings

    Attributes:
        min_word_threshold: Minimum words required for a chunk to be kept.
        threshold: Base score threshold (0.0-1.0). Nodes below are removed.
        threshold_type: "fixed" uses threshold directly; "dynamic" adjusts
            based on tag importance, text ratio, and link ratio.
    """

    def __init__(
        self,
        min_word_threshold: Optional[int] = None,
        threshold: float = 0.48,
        threshold_type: str = "fixed",
    ):
        self.min_word_threshold = min_word_threshold
        self.threshold = threshold
        self.threshold_type = threshold_type

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def filter_content(self, html: str, min_word_threshold: Optional[int] = None) -> list[str]:
        """
        Filter HTML content using the pruning algorithm.

        Args:
            html: Raw HTML to filter.
            min_word_threshold: Override the instance-level word threshold.

        Returns:
            List of HTML string chunks (as <tag>...</tag> elements).
        """
        if not html or not isinstance(html, str):
            return []

        soup = BeautifulSoup(html, "lxml")
        if not soup.body:
            soup = BeautifulSoup(f"<body>{html}</body>", "lxml")

        self._remove_comments(soup)
        self._remove_unwanted_tags(soup)

        body = soup.find("body")
        self._prune_tree(body, min_word_threshold or self.min_word_threshold)

        # Collect remaining top-level children as content blocks
        content_blocks: list[str] = []
        for element in body.children:
            if isinstance(element, str) or not hasattr(element, "name"):
                continue
            if len(element.get_text(strip=True)) > 0:
                content_blocks.append(str(element))

        return content_blocks

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _remove_comments(soup: BeautifulSoup) -> None:
        """Remove all HTML comments from the soup."""
        for element in soup(string=lambda s: isinstance(s, Comment)):
            element.extract()

    @staticmethod
    def _remove_unwanted_tags(soup: BeautifulSoup) -> None:
        """Remove unwanted tags (nav, script, style, etc.)."""
        for tag in EXCLUDED_TAGS:
            for element in soup.find_all(tag):
                element.decompose()

    def _prune_tree(self, node, min_word_threshold: Optional[int] = None) -> None:
        """
        Recursively prune the tree from the given node.

        Each node is scored; if below threshold it is removed.
        Otherwise, children are pruned recursively.
        """
        if not node or not hasattr(node, "name") or node.name is None:
            return

        text_len = len(node.get_text(strip=True))
        tag_len = len(node.encode_contents().decode("utf-8"))

        link_text_len = 0
        for a in node.find_all("a", recursive=False):
            s = a.string
            if s:
                link_text_len += len(s.strip())

        score = self._compute_composite_score(
            node, text_len, tag_len, link_text_len, min_word_threshold
        )

        if self.threshold_type == "fixed":
            should_remove = score < self.threshold
        else:
            should_remove = self._dynamic_should_remove(
                node, score, text_len, tag_len, link_text_len
            )

        if should_remove:
            node.decompose()
        else:
            children = [child for child in node.children if hasattr(child, "name")]
            for child in children:
                self._prune_tree(child, min_word_threshold)

    def _dynamic_should_remove(
        self, node, score: float, text_len: int, tag_len: int, link_text_len: int
    ) -> bool:
        """Determine if node should be removed using dynamic threshold."""
        tag_importance = TAG_IMPORTANCE.get(node.name, 0.7)
        text_ratio = text_len / tag_len if tag_len > 0 else 0
        link_ratio = link_text_len / text_len if text_len > 0 else 1

        threshold = self.threshold
        if tag_importance > 1:
            threshold *= 0.8
        if text_ratio > 0.4:
            threshold *= 0.9
        if link_ratio > 0.6:
            threshold *= 1.2

        return score < threshold

    def _compute_composite_score(
        self,
        node,
        text_len: int,
        tag_len: int,
        link_text_len: int,
        min_word_threshold: Optional[int] = None,
    ) -> float:
        """
        Compute a composite score (0.0-1.0) for a node.

        Combines: text_density, link_density, tag_weight,
        class_id_weight, and text_length.
        """
        if min_word_threshold:
            text = node.get_text(strip=True)
            word_count = text.count(" ") + 1
            if word_count < min_word_threshold:
                return -1.0  # Guaranteed removal

        score = 0.0
        total_weight = 0.0

        # 1. Text density: proportion of text within the tag
        density = text_len / tag_len if tag_len > 0 else 0
        score += METRIC_WEIGHTS["text_density"] * density
        total_weight += METRIC_WEIGHTS["text_density"]

        # 2. Link density: inverse — more links = lower quality
        link_density = 1 - (link_text_len / text_len if text_len > 0 else 0)
        score += METRIC_WEIGHTS["link_density"] * link_density
        total_weight += METRIC_WEIGHTS["link_density"]

        # 3. Tag weight: semantic importance of tag name
        tag_score = TAG_WEIGHTS.get(node.name, 0.5)
        score += METRIC_WEIGHTS["tag_weight"] * tag_score
        total_weight += METRIC_WEIGHTS["tag_weight"]

        # 4. Class/ID weight: penalise negative patterns
        class_score = self._compute_class_id_weight(node)
        score += METRIC_WEIGHTS["class_id_weight"] * max(0, class_score)
        total_weight += METRIC_WEIGHTS["class_id_weight"]

        # 5. Text length: longer blocks score higher (log scale)
        score += METRIC_WEIGHTS["text_length"] * math.log(text_len + 1)
        total_weight += METRIC_WEIGHTS["text_length"]

        return score / total_weight if total_weight > 0 else 0

    @staticmethod
    def _compute_class_id_weight(node) -> float:
        """
        Compute penalty based on class and id attributes.

        Returns:
            Negative values for known non-content patterns.
        """
        class_id_score = 0.0
        if "class" in node.attrs:
            classes = " ".join(node["class"])
            if NEGATIVE_PATTERNS.search(classes):
                class_id_score -= 0.5
        if "id" in node.attrs:
            element_id = node["id"]
            if NEGATIVE_PATTERNS.search(element_id):
                class_id_score -= 0.5
        return class_id_score
