"""17k.com novel site adapter — Aliyun WAF bypass via Playwright stealth + CloakBrowser."""
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


def _build_stealth_js() -> str:
    """Build stealth JS to inject into Playwright pages."""
    return """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
    window.chrome = { runtime: {} };
    Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
"""


@register_adapter
class Novel17kAdapter(SiteAdapter):
    """Adapter for 17k.com — Playwright stealth优先, CloakBrowser兜底."""

    URL_PATTERNS = [
        r"(?:www\.)?17k\.com/book/(\d+)",
        r"(?:www\.)?17k\.com/chapter/(\d+)/(\d+)",
    ]

    def __init__(self, headless: bool = True):
        self._cache: dict[int, BookInfo] = {}
        self._headless = headless

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

    async def _fetch_via_playwright(self, url: str) -> str:
        """Playwright stealth: 无头/有头均可, 注入反检测JS."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ImportError("playwright not installed")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=self._headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
            )
            page = await context.new_page()
            await page.add_init_script(_build_stealth_js())
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(5000)
            html = await page.content()
            await browser.close()
            return html

    async def _fetch_via_cloakbrowser(self, url: str) -> str:
        """CloakBrowser兜底: 有头模式, 过强WAF."""
        import cloakbrowser
        browser = await cloakbrowser.launch_async(headless=self._headless)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            return html
        finally:
            try:
                await browser.close()
            except Exception:
                pass

    async def _fetch_page(self, url: str) -> str:
        """智能降级: Playwright stealth → CloakBrowser."""
        try:
            html = await self._fetch_via_playwright(url)
            # 检测是否有WAF拦截
            if html and "人机验证" not in html and len(html) > 500:
                return html
            logger.info("Playwright stealth 被WAF拦截, 降级到CloakBrowser...")
        except Exception as e:
            logger.warning("Playwright stealth 失败: %s, 降级到CloakBrowser...", e)
        return await self._fetch_via_cloakbrowser(url)

    async def _fetch_chapter_list(self, book_id: int) -> List[Chapter]:
        """Fetch chapter list — Playwright stealth优先, CloakBrowser兜底."""
        url = f"https://www.17k.com/list/{book_id}.html"
        html = await self._fetch_page(url)

        chapters = []
        import re

        # 从HTML提取章节链接
        links = re.findall(r'href="([^"]*chapter/%d/(\d+)[^"]*)"[^>]*>([^<]+)<' % book_id, html)
        seen = set()
        for href, ch_id, title in links:
            title = title.strip()
            if ch_id in seen or not title:
                continue
            seen.add(ch_id)
            full_url = href if href.startswith("http") else f"https://www.17k.com{href}"
            chapters.append(Chapter(
                index=len(chapters) + 1,
                title=title,
                chapter_id=ch_id,
                url=full_url,
                is_vip=False,
            ))

        logger.info("17k 章节: %d 章 (book_id=%d)", len(chapters), book_id)
        return chapters

    def fetch_chapter(self, chapter: Chapter) -> str:
        return _run_async_safe(self._fetch_chapter_text(chapter))

    async def _fetch_chapter_text(self, chapter: Chapter) -> str:
        url = chapter.url or ""
        if not url:
            return ""

        html = await self._fetch_page(url)

        # 提取正文内容
        import re
        content_match = re.search(r'<div class="content">(.*?)</div>', html, re.DOTALL)
        if content_match:
            text = re.sub(r'<[^>]+>', '', content_match.group(1))
            text = re.sub(r'\s+', '\n', text).strip()
            if len(text) > 100:
                return text

        # 兜底: 提取所有段落
        paras = re.findall(r'<p>(.*?)</p>', html, re.DOTALL)
        texts = [re.sub(r'<[^>]+>', '', p).strip() for p in paras if len(p) > 20]
        if texts:
            return '\n'.join(texts)

        return ""

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        import os, time
        filename = f"{book.title or book.book_id}_{int(time.time())}.{output}"
        content_lines = []
        total = len(chapters)

        for i, ch in enumerate(chapters):
            text = self.fetch_chapter(ch)
            content_lines.append(f"\n\n第{ch.index}章 {ch.title}\n\n{text}\n")
            logger.info("下载进度: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)
            wc = len(text)
            self.simulate_read_delay(wc)
            self.simulate_inter_chapter_delay()

        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(content_lines))
        logger.info("下载完成: %s (%d 章, %s)", path, total, output.upper())
        return path
