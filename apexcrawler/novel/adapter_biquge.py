"""Biquge (笔趣阁) novel site adapter — generic for biquge mirrors."""
from __future__ import annotations
import asyncio
import logging
import re
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


try:
    import requests
except ImportError:
    requests = None  # 报错信息在 session 属性中处理

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


@register_adapter
class BiqugeAdapter(SiteAdapter):
    """Generic adapter for Biquge-style novel sites (biquge, bqg, etc.).

    Matches URLs like:
      https://www.biquge.com/book/12345/
      https://www.bqg.com/book/12345/
      https://www.xbiquge.la/book/12345/
      https://www.biquge.tv/book/12345/
    """

    # Common biquge-style domains
    DOMAINS = [
        "biquge", "bqg", "xbiquge", "biqugetv", "biqugeio",
        "biqugeinfo", "biqugezw", "biqugeabc", "biqugebu",
        "biquge5200", "biquge6", "biquge7", "biqugewu",
        "biqugewin", "biqugenet", "biqugeco", "biqugecn",
    ]

    URL_PATTERNS = [
        r"^https?://(?:www\.)?(?:{d})\.[a-z]+/book/(\d+)",
    ]

    @classmethod
    def _build_patterns(cls) -> List[str]:
        domains = "|".join(cls.DOMAINS)
        return [p.format(d=domains) for p in cls.URL_PATTERNS]

    def __init__(self):
        self._session = None
        self._patterns = self._build_patterns()
        self._base_url = ""  # will be set during get_book_info

    @property
    def session(self):
        if self._session is None:
            if requests is None:
                raise ImportError("Missing dependency: requests → pip install requests")
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            # 绕过系统代理直连
            self._session.trust_env = False
        return self._session

    def _make_abs_url(self, href: str) -> str:
        """Convert relative URL to absolute using the base URL."""
        if not href:
            return ""
        if href.startswith("http"):
            return href
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            # Extract scheme + host from base URL
            import re
            m = re.match(r'(https?://[^/]+)', self._base_url)
            if m:
                return m.group(1) + href
            return "https://www.biquge7.com" + href
        # Relative to current path
        if self._base_url and not self._base_url.endswith("/"):
            return self._base_url + "/" + href
        return self._base_url + href

    def match(self, url: str) -> bool:
        return any(re.search(p, url) for p in self._patterns)

    def _extract_book_id(self, url: str) -> int:
        for p in self._patterns:
            m = re.search(p, url)
            if m:
                return int(m.group(1))
        raise ValueError(f"Cannot extract book_id from: {url}")

    def _soup(self, url: str) -> "BeautifulSoup":
        resp = self.session.get(url, timeout=15)
        resp.encoding = "utf-8"
        if BeautifulSoup is None:
            raise ImportError("Missing dependency: beautifulsoup4 → pip install beautifulsoup4")
        return BeautifulSoup(resp.text, "html.parser")

    def get_book_info(self, url: str) -> BookInfo:
        book_id = self._extract_book_id(url)
        self._base_url = url.rstrip("/")  # store base URL for relative resolution

        soup = self._soup(url.rstrip("/") + "/")

        # Extract book name
        title_el = (soup.select_one("h1") or soup.select_one(".book-name") or
                    soup.select_one(".info h2") or soup.select_one("#info h1") or
                    soup.select_one('[class*="bookName"]'))
        book_name = title_el.get_text(strip=True) if title_el else f"Book {book_id}"

        # Extract chapter list
        chapters = self._extract_chapters(soup, book_id)

        return BookInfo(
            book_id=str(book_id),
            title=book_name,
            chapters=chapters,
        )

    def _extract_chapters(self, soup: "BeautifulSoup", book_id: int) -> List[Chapter]:
        """Extract chapter list from biquge-style page."""
        # Common biquge chapter list containers
        chapter_links = []

        # Try multiple common selectors
        for selector in [
            "#list dd a", "#list a", ".listmain a", ".chapter-list a",
            ".list-chapters a", "#chapters a", ".chapterlist a",
            "ul.chapter li a", ".book-list a", "[class*='chapter'] a",
            "#detail-list a", ".catalog a",
        ]:
            links = soup.select(selector)
            if len(links) > 3:  # found meaningful chapter list
                chapter_links = links
                break

        # Fallback: any link with "chapter" or book_id in href
        if not chapter_links or len(chapter_links) < 3:
            chapter_links = soup.select(f'a[href*="/{book_id}/"]')
            if not chapter_links or len(chapter_links) < 3:
                chapter_links = soup.select('a[href*="chapter"]')
                if not chapter_links or len(chapter_links) < 3:
                    chapter_links = soup.select('a[href*=".html"]')
                    chapter_links = [a for a in chapter_links if re.search(r'\d+\.html', a.get('href', ''))]

        chapters = []
        seen = set()
        for i, a in enumerate(chapter_links, 1):
            href = a.get("href", "")
            if not href or href in seen or href.startswith("#"):
                continue
            seen.add(href)
            href = self._make_abs_url(href)
            if not href.startswith("http"):
                continue

            title = a.get_text(strip=True) or f"Chapter {i}"
            # Extract chapter_id from href
            ch_id_match = re.search(r'/(\d+)\.html', href)
            ch_id = ch_id_match.group(1) if ch_id_match else str(i)

            chapters.append(Chapter(
                index=i,
                title=title,
                chapter_id=ch_id,
                url=href,
                is_vip=False,
            ))

        logger.info("笔趣阁章节列表: %d 章", len(chapters))
        return chapters

    def fetch_chapter(self, chapter: Chapter) -> str:
        url = chapter.url
        if not url:
            return ""

        # Try requests first
        try:
            soup = self._soup(url)
        except Exception as e:
            logger.warning("获取章节失败 %s: %s", url, e)
            return ""

        text = self._extract_content(soup)
        if text:
            return text

        # Requests got dynamic page ("加载中..."), fall back to browser
        logger.info("章节页面为动态加载，降级到浏览器渲染: %s", url)
        return _run_async_safe(self._fetch_via_browser(url))

    def _extract_content(self, soup: "BeautifulSoup") -> str:
        """Extract chapter content from BeautifulSoup object."""
        for selector in [
            "#content", ".content", ".read-content", ".chapter-content",
            ".text", ".article-content", ".showtxt", ".chapter_content",
            ".book-content", ".yd_text2", "#chaptercontent",
            '[class*="content"]', '[class*="text"]',
        ]:
            el = soup.select_one(selector)
            if el:
                raw = el.get_text("\n", strip=True)
                if len(raw) > 100:
                    import re as _re
                    text = _re.sub(r'(?:^|\n)\s*(?:本章完|请记住本书首发域名|一秒记住|天才一秒记住|\
                                手机用户请浏览|提示：|推荐：|https?://\S+)\s*(?:\n|$)', '', raw)
                    return text.strip()

        # Fallback: extract all substantial paragraphs
        paragraphs = soup.select("p")
        texts = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 10]
        if texts:
            return "\n".join(texts)
        return ""

    async def _fetch_via_browser(self, url: str) -> str:
        """Fallback: use CloakBrowser for dynamically loaded pages."""
        import cloakbrowser
        browser = await cloakbrowser.launch_async(headless=False)
        try:
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await asyncio.sleep(3)

            text = await page.evaluate("""() => {
                const sel = document.querySelector('#content, .content, .read-content, .showtxt, [class*="content"]');
                if (sel) {
                    sel.querySelectorAll('script, style, .ad, .copy, .report').forEach(e => e.remove());
                    return sel.innerText.trim();
                }
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
            wc = len(text)
            self.simulate_read_delay(wc)
            self.simulate_inter_chapter_delay()

        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(content_lines))
        logger.info("下载完成: %s (%d 章, %s)", path, total, output.upper())
        return path
