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

    # 阅读行为模拟参数
    _COOKIE_ROTATE_CHAPTER_LIMIT: int = 150
    _COOKIE_ROTATE_TIME_LIMIT: float = 7200.0  # 2 小时

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

    # ── 反检测公共方法 ────────────────────────────────────────────────

    def simulate_read_delay(self, word_count: int) -> None:
        """模拟阅读章节的时间消耗。"""
        import random, time, math
        if word_count <= 0:
            word_count = 1000
        base_delay = random.uniform(2, 5)
        read_time = (word_count / 1000) * 60  # ~1分钟/千字
        total_delay = base_delay + read_time * random.uniform(0.5, 1.2)
        total_delay = min(total_delay, 180)  # 最多3分钟
        segments = max(1, int(total_delay / 15))
        seg_time = total_delay / segments
        for _ in range(segments):
            time.sleep(seg_time)
            if random.random() < 0.1:
                time.sleep(random.uniform(2, 8))

    def simulate_inter_chapter_delay(self) -> None:
        """模拟章节之间的等待。"""
        import random, time
        delay = random.uniform(1.0, 4.0)
        if random.random() < 0.15:
            delay += random.uniform(3, 12)
        time.sleep(delay)
