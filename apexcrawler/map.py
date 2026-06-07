"""Site URL discovery (Map) — extract all public URLs from a domain.

Uses robots.txt + sitemap.xml as primary source, with optional BFS crawl fallback.
"""
from __future__ import annotations
import gzip
import logging
import re
from typing import List, Optional, Set
from urllib.parse import urlparse, urljoin

logger = logging.getLogger(__name__)


async def fetch_url(url: str, timeout: int = 10) -> Optional[str]:
    """Fetch a URL and return its text content."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xml,*/*",
            })
            resp.raise_for_status()
            content = resp.content
            # Handle gzip content
            if url.endswith(".gz"):
                try:
                    content = gzip.decompress(content)
                except Exception:
                    pass
            return content.decode("utf-8", errors="replace")
    except Exception as e:
        logger.debug(f"Failed to fetch {url}: {e}")
        return None


async def parse_robots_txt(domain: str) -> List[str]:
    """Fetch and parse robots.txt to extract Sitemap URLs."""
    base_url = f"https://{domain}" if not domain.startswith("http") else domain
    robots_url = f"{base_url}/robots.txt"
    content = await fetch_url(robots_url)
    if not content:
        return []
    
    sitemaps = []
    for line in content.splitlines():
        if line.lower().startswith("sitemap:"):
            url = line.split(":", 1)[1].strip()
            if url:
                sitemaps.append(url)
    return sitemaps


async def parse_sitemap(url: str) -> List[str]:
    """Download and parse a sitemap.xml (or sitemap index), return list of URLs."""
    content = await fetch_url(url)
    if not content:
        return []
    
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(content)
    except Exception as e:
        logger.debug(f"Failed to parse sitemap XML {url}: {e}")
        return []
    
    # XML namespace handling
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[:root.tag.index("}") + 1]
    
    urls = []
    
    # Check if it's a sitemap index
    sitemap_tags = root.findall(f".//{ns}sitemap/{ns}loc")
    if sitemap_tags:
        # It's a sitemap index — recursively parse sub-sitemaps
        for loc in sitemap_tags:
            loc_text = (loc.text or "").strip()
            if loc_text:
                sub_urls = await parse_sitemap(loc_text)
                urls.extend(sub_urls)
    else:
        # Regular sitemap — extract URLs
        for loc in root.findall(f".//{ns}loc"):
            loc_text = (loc.text or "").strip()
            if loc_text:
                urls.append(loc_text)
    
    return urls


async def discover_via_crawl(domain: str, max_pages: int = 30) -> List[str]:
    """BFS crawl to discover URLs (fallback when no sitemap available)."""
    base_url = f"https://{domain}" if not domain.startswith("http") else domain
    parsed = urlparse(base_url)
    base_domain = parsed.netloc
    
    import collections
    
    to_visit = collections.deque([base_url])
    visited: Set[str] = set()
    urls: List[str] = []
    
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.warning("BeautifulSoup not available for BFS crawl")
        return []
    
    while to_visit and len(visited) < max_pages:
        url = to_visit.popleft()
        if url in visited:
            continue
        visited.add(url)
        
        content = await fetch_url(url, timeout=5)
        if not content:
            continue
        
        soup = BeautifulSoup(content, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full_url = urljoin(url, href)
            parsed_url = urlparse(full_url)
            
            # Only keep same-domain URLs
            if parsed_url.netloc and parsed_url.netloc != base_domain:
                continue
            
            # Clean URL — preserve path and query for uniqueness
            clean = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
            if parsed_url.query:
                clean += f"?{parsed_url.query}"
            if clean.endswith("/"):
                clean = clean[:-1]
            
            if clean and clean not in visited and clean not in to_visit:
                if parsed_url.path and parsed_url.path != "/":
                    urls.append(clean)
                    to_visit.append(clean)
    
    return urls


async def map_site(domain: str, depth: int = 0) -> dict:
    """Discover all public URLs for a domain.
    
    Args:
        domain: Domain name (e.g. "example.com") or full URL.
        depth: If > 0, also do BFS crawl of depth levels to find additional URLs.
    
    Returns:
        Dict with "urls" (list of discovered URLs), "source" (where they came from),
        and "count" (total number).
    """
    if domain.startswith("http"):
        parsed = urlparse(domain)
        domain = parsed.netloc
    
    all_urls: List[str] = []
    
    # Step 1: Parse robots.txt for sitemaps
    sitemaps = await parse_robots_txt(domain)
    
    # Step 2: Parse sitemaps
    sitemap_urls = []
    for sm in sitemaps:
        urls = await parse_sitemap(sm)
        sitemap_urls.extend(urls)
    
    all_urls.extend(sitemap_urls)
    source = "sitemap"
    
    # Step 3: BFS fallback if depth > 0
    if depth > 0 and (not all_urls or depth > 1):
        crawl_urls = await discover_via_crawl(domain, max_pages=depth * 10)
        # Deduplicate
        existing = set(all_urls)
        for u in crawl_urls:
            if u not in existing:
                all_urls.append(u)
        source = "sitemap+crawl" if sitemap_urls else "crawl"
    
    # Deduplicate and sort
    all_urls = sorted(set(all_urls))
    
    return {
        "domain": domain,
        "count": len(all_urls),
        "source": source,
        "urls": all_urls[:500],  # Limit output size
    }
