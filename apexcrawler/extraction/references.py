"""
Citation reference system for Markdown output.
Converts inline links to numbered citations.
Inspiration: Crawl4AI's convert_links_to_citations.
"""
import re
from urllib.parse import urljoin

# Pre-compiled regex pattern for markdown links
LINK_PATTERN = re.compile(
    r'!?\[((?:[^\[\]]|\[(?:[^\[\]]|\[[^\]]*\])*\])*)\]'
    r'\(((?:[^()\s]|\([^()]*\))*)(?:\s+"([^"]*)")?\)'
)


def fast_urljoin(base: str, url: str) -> str:
    """Fast URL joining for common cases without full urljoin overhead."""
    if url.startswith(("http://", "https://", "mailto:", "//")):
        return url
    if url.startswith("/"):
        if base.endswith("/"):
            return base[:-1] + url
        return base + url
    return urljoin(base, url)


def convert_to_citations(markdown: str, base_url: str = "") -> tuple[str, str]:
    """
    Convert markdown links to numbered citations.

    Each unique URL gets a sequential number. Duplicate URLs reuse the same number.
    Relative URLs are resolved against base_url.

    Args:
        markdown: Raw markdown text containing links.
        base_url: Base URL for resolving relative paths.

    Returns:
        Tuple of (converted_markdown, references_section).

    Example:
        >>> md, refs = convert_to_citations("Read [docs](https://example.com)")
        >>> md
        'Read docs⟨1⟩'
        >>> refs
        '\\n\\n## References\\n\\n⟨1⟩ https://example.com: docs\\n'
    """
    link_map: dict[str, tuple[int, str]] = {}
    url_cache: dict[str, str] = {}
    parts: list[str] = []
    last_end = 0
    counter = 1

    for match in LINK_PATTERN.finditer(markdown):
        parts.append(markdown[last_end : match.start()])
        text, url, title = match.groups()

        # Resolve relative URLs
        if base_url and not url.startswith(("http://", "https://", "mailto:")):
            if url not in url_cache:
                url_cache[url] = fast_urljoin(base_url, url)
            url = url_cache[url]

        # Assign number to each unique URL
        if url not in link_map:
            desc_parts: list[str] = []
            if title:
                desc_parts.append(title)
            if text and text != title:
                desc_parts.append(text)
            desc = ": " + " - ".join(desc_parts) if desc_parts else ""
            link_map[url] = (counter, desc)
            counter += 1

        num = link_map[url][0]
        if match.group(0).startswith("!"):
            parts.append(f"![{text}⟨{num}⟩]")
        else:
            parts.append(f"{text}⟨{num}⟩")
        last_end = match.end()

    parts.append(markdown[last_end:])
    converted_text = "".join(parts)

    # Build references section
    references_parts = ["\n\n## References\n\n"]
    for url, (num, desc) in sorted(link_map.items(), key=lambda x: x[1][0]):
        references_parts.append(f"⟨{num}⟩ {url}{desc}\n")

    return converted_text, "".join(references_parts)
