"""Base class for novel site adapters."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Chapter:
    index: int
    title: str
    chapter_id: str = ""
    is_vip: bool = False
    word_count: int = 0
    content: str = ""
    url: str = ""


@dataclass
class BookInfo:
    book_id: str
    title: str = ""
    author: str = ""
    description: str = ""
    cover_url: str = ""
    chapters: List[Chapter] = field(default_factory=list)


class SiteAdapter(ABC):
    """Abstract base for novel site adapters."""

    @abstractmethod
    def match(self, url: str) -> bool:
        """Return True if this adapter handles the given URL."""
        ...

    @abstractmethod
    def get_book_info(self, url: str) -> BookInfo:
        """Get book metadata and chapter list."""
        ...

    @abstractmethod
    def fetch_chapter(self, chapter: Chapter) -> str:
        """Fetch a single chapter's content. Returns text."""
        ...

    @abstractmethod
    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        """Download chapters and save to file. Returns file path."""
        ...
