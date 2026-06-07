"""Web search integration for ApexCrawler — Serper.dev + DuckDuckGo fallback."""
from __future__ import annotations
import json
import logging
import os
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

# ── Search result model ──

class SearchResult:
    """A single search result item."""
    def __init__(self, title: str, link: str, snippet: str = "", position: int = 0):
        self.title = title
        self.link = link
        self.snippet = snippet
        self.position = position
    
    def to_dict(self) -> dict:
        return {"title": self.title, "link": self.link, "snippet": self.snippet, "position": self.position}


# ── Serper.dev provider (primary) ──

def search_serper(query: str, num: int = 10, api_key: str = "") -> List[SearchResult]:
    """Search using Serper.dev API (free 2500 requests/month)."""
    if not api_key:
        api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        logger.warning("SERPER_API_KEY not set, Serper search disabled")
        return []
    
    try:
        import httpx
        resp = httpx.post(
            "https://google.serper.dev/search",
            json={"q": query, "num": min(num, 20)},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        results = []
        for i, item in enumerate(data.get("organic", [])):
            results.append(SearchResult(
                title=item.get("title", ""),
                link=item.get("link", ""),
                snippet=item.get("snippet", ""),
                position=i + 1,
            ))
        return results
    except Exception as e:
        logger.error(f"Serper search failed: {e}")
        return []


# ── DuckDuckGo provider (free fallback, no API key needed) ──

def search_duckduckgo(query: str, num: int = 10) -> List[SearchResult]:
    """Search using DuckDuckGo (free, no API key)."""
    try:
        # Use httpx to call DuckDuckGo's HTML search
        import httpx
        from bs4 import BeautifulSoup
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        resp = httpx.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }, timeout=10, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for i, result in enumerate(soup.select(".result"), 1):
            if i > num:
                break
            title_el = result.select_one(".result__title a")
            snippet_el = result.select_one(".result__snippet")
            if title_el:
                results.append(SearchResult(
                    title=title_el.get_text(strip=True),
                    link=title_el.get("href", ""),
                    snippet=snippet_el.get_text(strip=True) if snippet_el else "",
                    position=i,
                ))
        return results
    except ImportError:
        logger.warning("BeautifulSoup not available for DuckDuckGo search")
        return []
    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}")
        return []


# ── Unified search entry point ──

async def search_web(query: str, num: int = 10, provider: str = "auto") -> List[SearchResult]:
    """Search the web and return structured results.
    
    Args:
        query: Search query string.
        num: Number of results to return (max 20).
        provider: "serper", "duckduckgo", or "auto" (try serper first, fallback to duckduckgo).
    
    Returns:
        List of SearchResult objects.
    """
    results = []
    
    if provider in ("serper", "auto"):
        results = search_serper(query, num)
    
    if not results and provider in ("duckduckgo", "auto"):
        results = search_duckduckgo(query, num)
    
    return results
