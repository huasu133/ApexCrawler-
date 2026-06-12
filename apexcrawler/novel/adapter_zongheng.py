"""Zongheng (纵横中文网) novel site adapter — using curl_cffi for CAPTCHA bypass."""
from __future__ import annotations
import logging
import re
from typing import List, Optional
from apexcrawler.novel.adapter_base import SiteAdapter, BookInfo, Chapter
from apexcrawler.novel.engine import register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class ZonghengAdapter(SiteAdapter):
    """Adapter for zongheng.com — Baidu's novel platform.

    Uses curl_cffi for TLS fingerprint impersonation to bypass CAPTCHA.
    Mobile site (m.zongheng.com) is used for chapter content as it has
    server-rendered content without JavaScript dependency.

    URL patterns:
      Book info (main):  https://www.zongheng.com/detail/{book_id}
      Chapter (mobile):  https://m.zongheng.com/chapter/{book_id}/{chapter_id}.html
    """

    URL_PATTERNS = [
        r"(?:www\.)?zongheng\.com/detail/(\d+)",
        r"(?:www\.)?zongheng\.com/chapter/(\d+)/(\d+)",
        r"huayu\.zongheng\.com/book/(\d+)",
    ]

    def __init__(self):
        self._session = None

    def _get_session(self):
        if self._session is None:
            try:
                from curl_cffi import requests as curl_requests
            except ImportError:
                raise ImportError("Missing dependency: curl_cffi -> pip install curl_cffi")
            self._session = curl_requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
        return self._session

    def match(self, url: str) -> bool:
        return any(re.search(p, url) for p in self.URL_PATTERNS)

    def _extract_book_id(self, url: str) -> str:
        for p in self.URL_PATTERNS:
            m = re.search(p, url)
            if m:
                return m.group(1)
        raise ValueError(f"Cannot extract book_id from: {url}")

    def get_book_info(self, url: str) -> BookInfo:
        book_id = self._extract_book_id(url)
        s = self._get_session()

        # Build detail page URL
        if "huayu.zongheng.com" in url:
            detail_url = f"https://huayu.zongheng.com/book/{book_id}.html"
        else:
            detail_url = f"https://www.zongheng.com/detail/{book_id}"

        resp = s.get(detail_url, impersonate="chrome131", timeout=15)
        resp.encoding = "utf-8"
        html = resp.text

        # Extract title from <title> tag
        title = f"Book {book_id}"
        title_match = re.search(r'<title>(.*?)最新章节.*?</title>', html)
        if title_match:
            title = title_match.group(1).strip()
        else:
            title_match = re.search(r'<title>(.*?)</title>', html)
            if title_match:
                title = title_match.group(1).strip()

        # Extract author from NUXT data or page text
        author = ""
        for p in [r'"author_name"\s*:\s*"([^"]+)"', r'作者[：:]\s*([^<\n&]{2,20})',
                   r'"nickName"\s*:\s*"([^"]+)"', r'"author"\s*:\s*"([^"]+)"']:
            m = re.search(p, html)
            if m:
                author = m.group(1).strip()
                break

        # Try to get chapter list from mobile catalog page
        chapters = self._fetch_chapters(book_id)

        return BookInfo(book_id=book_id, title=title, author=author, chapters=chapters)

    def _fetch_chapters(self, book_id: str) -> List[Chapter]:
        """Fetch chapter list — extract what we can from static HTML.

        Zongheng uses client-side rendering for the full chapter list.
        We can extract first and latest chapter IDs from the NUXT data.
        For the full list, browser rendering (Playwright/CloakBrowser) is needed.
        """
        s = self._get_session()
        chapters = []

        detail_url = f"https://www.zongheng.com/detail/{book_id}"
        resp = s.get(detail_url, impersonate="chrome131", timeout=15)
        resp.encoding = "utf-8"
        html = resp.text

        # Extract chapter info from NUXT/page data
        first_name = ""
        latest_id = ""
        latest_name = ""

        m = re.search(r'firstChapterName[:\s]*"([^"]+)"', html)
        if m:
            first_name = m.group(1)
        m = re.search(r'latestChapterId[:\s]*(\d+)', html)
        if m:
            latest_id = m.group(1)
        m = re.search(r'latestChapterName[:\s]*"([^"]+)"', html)
        if m:
            latest_name = m.group(1)

        # Find first chapter ID from chapter URLs in the page
        ch_ids = re.findall(r'/chapter/\d+/(\d+)', html)
        first_id = ch_ids[0] if ch_ids else ""

        # Build what we can
        if first_id:
            chapters.append(Chapter(
                index=1,
                title=first_name or f"\u7b2c1\u7ae0",
                chapter_id=first_id,
                url=f"https://m.zongheng.com/chapter/{book_id}/{first_id}.html",
            ))

        if latest_id and latest_id != first_id:
            chapters.append(Chapter(
                index=999,
                title=latest_name or f"\u6700\u65b0\u7ae0\u8282",
                chapter_id=latest_id,
                url=f"https://m.zongheng.com/chapter/{book_id}/{latest_id}.html",
            ))

        logger.info("纵横章节: first=%s(%s), latest=%s(%s) - 完整列表需浏览器渲染",
                     first_id, first_name, latest_id, latest_name)
        return chapters

    def fetch_chapter(self, chapter: Chapter) -> str:
        """Fetch chapter content from mobile site (server-rendered, no CAPTCHA)."""
        url = chapter.url
        if not url:
            return ""

        # Ensure we use mobile URL
        if "www.zongheng.com" in url:
            url = url.replace("www.zongheng.com", "m.zongheng.com")

        try:
            s = self._get_session()
            resp = s.get(url, impersonate="chrome131", timeout=15)
            resp.encoding = "utf-8"
            html = resp.text
        except Exception as e:
            logger.warning("获取章节失败 %s: %s", url, e)
            return ""

        # Extract content from mobile page
        # Mobile page has server-rendered content in various containers
        content_selectors = [
            r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="content"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*chapter-content[^"]*"[^>]*>(.*?)</div>',
            r'<article[^>]*>(.*?)</article>',
            r'<div[^>]*class="[^"]*read-content[^"]*"[^>]*>(.*?)</div>',
        ]

        for pattern in content_selectors:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                text = re.sub(r'<[^>]+>', '', m.group(1))
                text = re.sub(r'\s*\n\s*', '\n', text).strip()
                if len(text) > 100:
                    # Clean up noise
                    text = re.sub(
                        r'(?:^|\n)\s*(?:本章完|请记住本书首发域名|一秒记住|天才一秒记住|'
                        r'手机用户请浏览|提示：|推荐：|https?://\S+|'
                        r'纵横中文网.*?www\.zongheng\.com|快捷键|听书|'
                        r'下一章|上一章|返回目录)\s*(?:\n|$)',
                        '', text, flags=re.IGNORECASE
                    )
                    return text.strip()

        # Fallback: extract substantial paragraphs
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
        texts = []
        for p in paragraphs:
            t = re.sub(r'<[^>]+>', '', p).strip()
            if len(t) > 10:
                texts.append(t)
        if texts:
            return "\n".join(texts)

        return ""

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        import os
        filename = f"{book.title or book.book_id}_{int(time.time())}.{output}"
        content_lines = []
        total = len(chapters)

        for i, ch in enumerate(chapters):
            text = self.fetch_chapter(ch)
            content_lines.append(f"\n\n\u7b2c{ch.index}\u7ae0 {ch.title}\n\n{text}\n")
            logger.info("下载进度: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)

        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(content_lines))
        logger.info("下载完成: %s (%d 章, %s)", path, total, output.upper())
        return path
