"""Unified novel engine with URL auto-detection."""
from __future__ import annotations
import logging
from typing import List, Optional
from apexcrawler.novel.adapter_base import SiteAdapter, BookInfo, Chapter

logger = logging.getLogger(__name__)

_registered_adapters: list[type[SiteAdapter]] = []


def register_adapter(adapter_cls: type[SiteAdapter]):
    _registered_adapters.append(adapter_cls)
    return adapter_cls


class NovelEngine:
    """Unified entry point for novel crawling."""

    def __init__(self):
        self._adapters: list[SiteAdapter] = []
        self._load_adapters()

    def _load_adapters(self):
        for cls in _registered_adapters:
            try:
                self._adapters.append(cls())
            except Exception as e:
                logger.debug("Failed to load adapter %s: %s", cls.__name__, e)

    def _detect(self, url: str) -> SiteAdapter:
        for adp in self._adapters:
            if adp.match(url):
                return adp
        raise ValueError(f"No adapter found for URL: {url}")

    def info(self, url: str) -> BookInfo:
        """Get book info and chapter list."""
        adp = self._detect(url)
        return adp.get_book_info(url)

    def chapter(self, url_or_chapter) -> str:
        """Fetch a single chapter."""
        if isinstance(url_or_chapter, str):
            adp = self._detect(url_or_chapter)
            return adp.fetch_chapter(Chapter(index=0, title="", url=url_or_chapter))
        elif isinstance(url_or_chapter, Chapter):
            url = url_or_chapter.url or ""
            if url:
                adp = self._detect(url)
                return adp.fetch_chapter(url_or_chapter)
            return ""
        return ""

    def download(self, url: str, start: int = 1, end: int = 0, output: str = "txt") -> str:
        """Download chapters range from a novel URL."""
        adp = self._detect(url)
        book = adp.get_book_info(url)
        chapters = book.chapters
        if end > 0:
            chapters = [c for c in chapters if start <= c.index <= end]
        elif start > 1:
            chapters = [c for c in chapters if c.index >= start]
        return adp.download(book, chapters, output=output)
