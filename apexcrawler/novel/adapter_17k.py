"""17k.com novel site adapter — Aliyun WAF bypass via CloakBrowser."""
from __future__ import annotations
import asyncio
import logging
from typing import List, Optional
from apexcrawler.novel.adapter_base import SiteAdapter, BookInfo, Chapter
from apexcrawler.novel.engine import register_adapter

logger = logging.getLogger(__name__)


def _run_async_safe(coro):
    """Safely run a coroutine from sync context, even if loop is running."""
    try:
        loop = asyncio.get_running_loop()
        import threading
        result = []
        error = []

        def _run():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            try:
                r = new_loop.run_until_complete(coro)
                result.append(r)
            except Exception as e:
                error.append(e)
            finally:
                new_loop.close()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join()
        if error:
            raise error[0]
        return result[0]
    except RuntimeError:
        return asyncio.run(coro)


@register_adapter
class Novel17kAdapter(SiteAdapter):
    """Adapter for 17k.com using CloakBrowser to bypass Aliyun WAF."""

    URL_PATTERNS = [
        r"(?:www\.)?17k\.com/book/(\d+)",
        r"(?:www\.)?17k\.com/chapter/(\d+)/(\d+)",
    ]

    def __init__(self):
        self._cache: dict[int, BookInfo] = {}

    def _extract_book_id(self, url: str) -> int:
        import re
        for p in self.URL_PATTERNS:
            m = re.search(p, url)
            if m:
                return int(m.group(1))
        raise ValueError(f"Cannot extract book_id from: {url}")

    def match(self, url: str) -> bool:
        import re
        return any(re.search(p, url) for p in self.URL_PATTERNS)

    def get_book_info(self, url: str) -> BookInfo:
        book_id = self._extract_book_id(url)
        if book_id in self._cache:
            return self._cache[book_id]

        chapters = _run_async_safe(self._fetch_chapter_list(book_id))
        book = BookInfo(
            book_id=str(book_id),
            title=f"Book {book_id}",
            chapters=chapters,
        )
        self._cache[book_id] = book
        return book

    async def _fetch_chapter_list(self, book_id: int) -> List[Chapter]:
        """Fetch chapter list from 17k.com via CloakBrowser."""
        import cloakbrowser

        browser = await cloakbrowser.launch_async(headless=False)
        try:
            page = await browser.new_page()
            url = f"https://www.17k.com/list/{book_id}.html"
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await asyncio.sleep(3)

            chapters = await page.evaluate(f"""() => {{
                const links = Array.from(document.querySelectorAll('a[href*="/chapter/{book_id}/"]'));
                if (links.length === 0) {{
                    // fallback: any chapter link
                    return Array.from(document.querySelectorAll('a[href*="/chapter/"]'))
                        .filter(a => a.href.includes('{book_id}'))
                        .map((a, i) => ({{ index: i + 1, title: a.innerText.trim(), href: a.href }}));
                }}
                return links.map((a, i) => ({{
                    index: i + 1,
                    title: a.innerText.trim(),
                    href: a.href
                }}));
            }}""")

            result = []
            for ch in chapters:
                title = ch.get("title", "") or ""
                href = ch.get("href", "") or ""
                ch_id = 0
                if href:
                    import re
                    m = re.search(r"/chapter/\d+/(\d+)", href)
                    if m:
                        ch_id = int(m.group(1))
                result.append(Chapter(
                    index=ch.get("index", 0),
                    title=title,
                    chapter_id=str(ch_id),
                    url=href,
                    is_vip=False,
                ))

            logger.info("17k 章节列表获取完成: book_id=%d, %d 章", book_id, len(result))
            return result

        finally:
            await browser.close()

    def fetch_chapter(self, chapter: Chapter) -> str:
        return _run_async_safe(self._fetch_chapter_text(chapter))

    async def _fetch_chapter_text(self, chapter: Chapter) -> str:
        import cloakbrowser
        url = chapter.url or ""
        if not url:
            return ""

        browser = await cloakbrowser.launch_async(headless=False)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await asyncio.sleep(2)

            text = await page.evaluate("""() => {
                const sel = document.querySelector('.content');
                if (sel) {
                    // remove unwanted elements
                    sel.querySelectorAll('script, style, .chapter-tip, .chapter-nav, .ad, [class*="ad-"], [class*="tip"], .copy, .report, .author-say').forEach(e => e.remove());
                    return sel.innerText.trim();
                }
                const fallback = document.querySelector('#content, .read-content, .text, .chapter-content, .article-content');
                if (fallback) return fallback.innerText.trim();
                return document.body.innerText.trim();
            }""")
            return text or ""
        finally:
            await browser.close()

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        import os, time
        filename = f"{book.title or book.book_id}_{int(time.time())}.{output}"
        content_lines = []
        total = len(chapters)

        for i, ch in enumerate(chapters):
            text = self.fetch_chapter(ch)
            content_lines.append(f"\n\n第{ch.index}章 {ch.title}\n\n{text}\n")
            logger.info("下载进度: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)

        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(content_lines))
        logger.info("下载完成: %s (%d 章, %s)", path, total, output.upper())
        return path
