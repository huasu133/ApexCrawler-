"""17k.com novel site adapter — Aliyun WAF bypass via Playwright stealth + CloakBrowser."""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Any, List, Optional
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


_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN','zh','en']});
window.chrome = { runtime: {} };
"""


async def _fetch_via_playwright(url: str, js: str, headless: bool = True) -> str:
    """使用 Playwright stealth 渲染页面并执行 JS，返回 JSON 字符串结果。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise ImportError("playwright not installed")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        try:
            page = await browser.new_page(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                locale="zh-CN",
            )
            await page.add_init_script(_STEALTH_JS)
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(3000)
            result = await page.evaluate(js)
            return json.dumps(result, ensure_ascii=False) if result else ""
        finally:
            await browser.close()


async def _fetch_via_cloakbrowser(url: str, js: str, headless: bool = True) -> str:
    """使用 CloakBrowser 渲染页面并执行 JS，返回 JSON 字符串结果。"""
    import cloakbrowser
    browser = await cloakbrowser.launch_async(headless=headless)
    try:
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_timeout(3000)
        result = await page.evaluate(js)
        return json.dumps(result, ensure_ascii=False) if result else ""
    finally:
        try:
            await browser.close()
        except Exception:
            pass


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

    async def _eval_on_page(self, url: str, js: str) -> Any:
        """智能降级: Playwright stealth → CloakBrowser, 返回JS执行结果."""
        # 先试 Playwright stealth
        try:
            result = await _fetch_via_playwright(url, js, headless=self._headless)
            if result and len(result) > 10:
                return json.loads(result)
        except Exception as e:
            logger.warning("Playwright stealth 失败: %s, 降级到 CloakBrowser...", e)

        # 兜底 CloakBrowser
        try:
            result = await _fetch_via_cloakbrowser(url, js, headless=self._headless)
            if result:
                return json.loads(result)
        except Exception as e:
            logger.warning("CloakBrowser 也失败: %s", e)

        return None

    async def _fetch_chapter_list(self, book_id: int) -> List[Chapter]:
        """Fetch chapter list — 浏览器渲染提取."""
        js = f"""() => {{
            const links = Array.from(document.querySelectorAll('a[href*="/chapter/{book_id}/"]'));
            if (links.length === 0) {{
                return Array.from(document.querySelectorAll('a[href*="/chapter/"]'))
                    .filter(a => a.href.includes('{book_id}'))
                    .map((a, i) => ({{ index: i + 1, title: a.innerText.trim(), href: a.href }}));
            }}
            return links.map((a, i) => ({{
                index: i + 1,
                title: a.innerText.trim(),
                href: a.href
            }}));
        }}"""

        url = f"https://www.17k.com/list/{book_id}.html"
        chapters_data = await self._eval_on_page(url, js)

        result = []
        if chapters_data and isinstance(chapters_data, list):
            import re
            for ch in chapters_data:
                title = ch.get("title", "") or ""
                href = ch.get("href", "") or ""
                ch_id = 0
                if href:
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

        logger.info("17k 章节: %d 章 (book_id=%d)", len(result), book_id)
        return result

    def fetch_chapter(self, chapter: Chapter) -> str:
        return _run_async_safe(self._fetch_chapter_text(chapter))

    async def _fetch_chapter_text(self, chapter: Chapter) -> str:
        url = chapter.url or ""
        if not url:
            return ""

        js = """() => {
            const sel = document.querySelector('.content');
            if (sel) {
                sel.querySelectorAll('script, style, .chapter-tip, .chapter-nav, .ad, [class*="ad-"], [class*="tip"], .copy, .report, .author-say').forEach(e => e.remove());
                return sel.innerText.trim();
            }
            const fallback = document.querySelector('#content, .read-content, .text, .chapter-content, .article-content');
            if (fallback) return fallback.innerText.trim();
            return document.body.innerText.trim();
        }"""

        text = await self._eval_on_page(url, js)
        return str(text or "")

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        import os, time, re
        filename = f"{book.title or book.book_id}_{int(time.time())}.{output}"
        content_lines = []
        total = len(chapters)
        skipped = 0

        for i, ch in enumerate(chapters):
            text = self.fetch_chapter(ch)
            # 跳过未完整更新的章节（字数过少）
            if len(text.strip()) < 200:
                logger.info("跳过未完成章节 (%d字): %s", len(text.strip()), ch.title)
                skipped += 1
                continue
            # 标题已含章节号时不重复加前缀
            if re.match(r'^第(?:\d+|[一二三四五六七八九十百千零]+)[章节]', ch.title):
                title_line = ch.title
            else:
                title_line = f"第{ch.index}章 {ch.title}"
            content_lines.append(f"\n\n{title_line}\n\n{text}\n")
            logger.info("下载进度: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)
            wc = len(text)
            self.simulate_read_delay(wc)
            self.simulate_inter_chapter_delay()

        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(content_lines))
        logger.info("下载完成: %s (%d 章, 跳过%d章未完成)", path, total - skipped, skipped)
        return path
