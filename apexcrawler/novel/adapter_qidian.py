"""Qidian novel site adapter — wraps existing QidianEngine."""
from __future__ import annotations
import logging
from typing import List, Optional
from apexcrawler.novel.adapter_base import SiteAdapter, BookInfo, Chapter
from apexcrawler.novel.engine import register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class QidianAdapter(SiteAdapter):
    """Adapter for Qidian.com using the existing QidianEngine."""

    URL_PATTERNS = [
        r"book\.qidian\.com/info/(\d+)",
        r"www\.qidian\.com/book/(\d+)",
        r"www\.qidian\.com/chapter/(\d+)",
        r"qidian\.com/(?:book|info|chapter)/(\d+)",
    ]

    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        if self._engine is None:
            from apexcrawler.engines.qidian import QidianEngine
            self._engine = QidianEngine(headless=True)
        return self._engine

    def match(self, url: str) -> bool:
        import re
        return any(re.search(p, url) for p in self.URL_PATTERNS)

    def _extract_book_id(self, url: str) -> int:
        import re
        for p in self.URL_PATTERNS:
            m = re.search(p, url)
            if m:
                return int(m.group(1))
        raise ValueError(f"Cannot extract book_id from: {url}")

    def get_book_info(self, url: str) -> BookInfo:
        book_id = self._extract_book_id(url)
        qidian_chapters = self.engine.fetch_catalog(book_id)
        chapters = [
            Chapter(
                index=c.index,
                title=c.title,
                chapter_id=str(c.chapter_id),
                is_vip=c.is_vip,
                word_count=c.word_count,
                url=c.url,
            )
            for c in qidian_chapters
        ]
        return BookInfo(
            book_id=str(book_id),
            title=f"Book {book_id}",
            chapters=chapters,
        )

    def fetch_chapter(self, chapter: Chapter) -> str:
        from apexcrawler.engines.qidian import Chapter as QChapter
        qc = QChapter(
            chapter_id=int(chapter.chapter_id) if chapter.chapter_id else 0,
            book_id=0,
            title=chapter.title,
            index=chapter.index,
            url=chapter.url,
        )
        result = self.engine.fetch_chapter(qc)
        return result.content or ""

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        import os, time
        from apexcrawler.engines.qidian import Chapter as QChapter

        filename = f"{book.title or book.book_id}_{int(time.time())}.{output}"

        # 转换为引擎内部的 Chapter 对象
        engine_chapters = [
            QChapter(
                chapter_id=int(ch.chapter_id) if ch.chapter_id else 0,
                book_id=int(book.book_id),
                title=ch.title,
                index=ch.index,
                is_vip=ch.is_vip,
                url=ch.url,
            )
            for ch in chapters
        ]

        # 使用引擎的批量获取（如果 curl_cffi 被 WAF 拦截，自动降级到单浏览器会话批量渲染）
        fetched = self.engine.fetch_chapters(engine_chapters)

        content_lines = []
        total = len(fetched)
        for i, ch in enumerate(fetched):
            text = ch.content or ""
            content_lines.append(f"\n\n第{ch.index}章 {ch.title}\n\n{text}\n")
            logger.info("下载进度: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)

        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(content_lines))

        logger.info("下载完成: %s (%d 章, %s)", path, total, output.upper())
        return path
