"""Zongheng (纵横中文网) novel site adapter — hybrid Playwright + curl_cffi."""
from __future__ import annotations
import logging
import re
from typing import List, Optional
from apexcrawler.novel.adapter_base import SiteAdapter, BookInfo, Chapter
from apexcrawler.novel.engine import register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class ZonghengAdapter(SiteAdapter):
    """Adapter for zongheng.com — hybrid approach:

    - Chapter list: Playwright + system Chrome profile (user cookies bypass CAPTCHA)
    - Chapter content: curl_cffi + mobile site (m.zongheng.com, server-rendered)
    """

    URL_PATTERNS = [
        r"(?:www\.)?zongheng\.com/detail/(\d+)",
        r"(?:www\.)?zongheng\.com/chapter/(\d+)/(\d+)",
        r"huayu\.zongheng\.com/book/(\d+)",
    ]

    _cached_chapters: dict[str, List[Chapter]] = {}

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
            })
            # 注入系统Chrome的登录Cookie（用于访问付费章节）
            self._inject_chrome_cookies()
        return self._session

    def _inject_chrome_cookies(self):
        """从系统Chrome用户数据注入.zongheng.com的Cookie到curl_cffi会话。

        默认读取默认Chrome配置的Cookie。可通过 ZONGHENG_CHROME_USER_DATA 环境变量
        指定其他Chrome用户数据目录（如白号专用Profile）。
        """
        import os, sys
        user_data_dir = os.environ.get(
            "ZONGHENG_CHROME_USER_DATA",
            os.path.expanduser("~/Library/Application Support/Google/Chrome/Default")
            if sys.platform == "darwin"
            else os.path.expanduser("~/.config/google-chrome/Default"),
        )
        cookie_file = os.path.join(user_data_dir, "Cookies")
        if not os.path.exists(cookie_file):
            logger.debug("Chrome cookie文件不存在: %s, 跳过注入", cookie_file)
            return
        try:
            import sqlite3
            conn = sqlite3.connect(cookie_file)
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT name, value FROM cookies WHERE host_key LIKE '%zongheng.com%'"
            ).fetchall()
            conn.close()
            for name, value in rows:
                self._session.cookies.set(name, value, domain=".zongheng.com")
            logger.info("已注入 %d 个 Chrome Cookie (纵横登录态)", len(rows))
        except Exception as e:
            logger.warning("注入Chrome Cookie失败: %s", e)

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

        # Get detail page for title/author
        detail_url = f"https://www.zongheng.com/detail/{book_id}"
        resp = s.get(detail_url, impersonate="chrome131", timeout=15)
        resp.encoding = "utf-8"
        html = resp.text

        # Extract title
        title = f"Book {book_id}"
        m = re.search(r'<title>(.*?)最新章节.*?</title>', html)
        if m:
            title = m.group(1).strip()
        else:
            m = re.search(r'<title>(.*?)</title>', html)
            if m:
                title = m.group(1).strip()

        # Extract author
        author = ""
        for p in [r'"author_name"\s*:\s*"([^"]+)"', r'"nickName"\s*:\s*"([^"]+)"', r'"author"\s*:\s*"([^"]+)"']:
            m = re.search(p, html)
            if m:
                author = m.group(1).strip()
                break
        if not author:
            m = re.search(r'作者[：:]\s*([^<\n&]{2,20})', html)
            if m:
                author = m.group(1).strip()

        # Get chapter list (cached or via Playwright)
        chapters = self._get_chapters(book_id)

        return BookInfo(book_id=book_id, title=title, author=author, chapters=chapters)

    def _get_chapters(self, book_id: str) -> List[Chapter]:
        """Get chapter list — from cache or via Playwright with user Chrome profile."""
        if book_id in self._cached_chapters:
            return self._cached_chapters[book_id]

        chapters = self._fetch_chapters_via_playwright(book_id)

        # Cache for this session
        if chapters:
            self._cached_chapters[book_id] = chapters

        return chapters

    def _get_chrome_paths(self):
        """Get Chrome user data dir and executable path from env or defaults."""
        import os, sys

        user_data_dir = os.environ.get(
            "ZONGHENG_CHROME_USER_DATA",
            os.path.expanduser("~/Library/Application Support/Google/Chrome/Default")
            if sys.platform == "darwin"
            else os.path.expanduser("~/.config/google-chrome/Default"),
        )
        executable_path = os.environ.get(
            "ZONGHENG_CHROME_PATH",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
            if sys.platform == "darwin"
            else "/usr/bin/google-chrome",
        )
        return user_data_dir, executable_path

    def _fetch_chapters_via_playwright(self, book_id: str) -> List[Chapter]:
        """Fetch full chapter list by capturing the bookapi.zongheng.com API response."""
        chapters = []
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("Playwright not installed, using limited chapter list")
            return self._fetch_chapters_fallback(book_id)

        user_data_dir, chrome_path = self._get_chrome_paths()

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=False,
                    executable_path=chrome_path,
                    args=["--no-sandbox"],
                )
                try:
                    page = browser.pages[0] if browser.pages else browser.new_page()

                    # Capture the chapter list API response
                    api_response = [None]
                    def handle_response(response):
                        if "getChapterList" in response.url:
                            api_response[0] = response
                    page.on("response", handle_response)

                    # Navigate and click catalog tab to trigger API call
                    page.goto(f"https://www.zongheng.com/detail/{book_id}",
                              wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                    page.click("text=\u76ee\u5f55", timeout=5000)
                    page.wait_for_timeout(3000)

                    # Parse the captured API response
                    if api_response[0]:
                        data = api_response[0].json()
                        chapter_lists = data.get("result", {}).get("chapterList", [])
                        for volume in chapter_lists:
                            for c in volume.get("chapterViewList", []):
                                ch_id = str(c.get("chapterId", ""))
                                ch_name = c.get("chapterName", "")
                                word_count = c.get("wordNums", 0)
                                ch_price = c.get("price", 0)  # 纵横币, >0为付费章节
                                if ch_id and ch_name:
                                    chapters.append(Chapter(
                                        index=len(chapters) + 1,
                                        title=ch_name,
                                        chapter_id=ch_id,
                                        url=f"https://m.zongheng.com/chapter/{book_id}/{ch_id}.html",
                                        word_count=int(word_count),
                                        is_vip=(ch_price > 0),  # price>0为付费
                                    ))
                finally:
                    try:
                        browser.close()
                    except Exception:
                        pass

        except Exception as e:
            logger.warning("Playwright chapter list failed: %s", e)
            return self._fetch_chapters_fallback(book_id)

        logger.info("纵横章节列表: %d 章 (via Playwright API)", len(chapters))
        return chapters

    def _fetch_chapters_fallback(self, book_id: str) -> List[Chapter]:
        """Fallback: extract first/last chapter from page HTML."""
        chapters = []
        s = self._get_session()
        resp = s.get(f"https://www.zongheng.com/detail/{book_id}", impersonate="chrome131", timeout=15)
        resp.encoding = "utf-8"
        html = resp.text

        first_name = ""
        latest_id = ""
        latest_name = ""
        ch_ids = re.findall(r'/chapter/\d+/(\d+)', html)

        m = re.search(r'firstChapterName[:\s]*"([^"]+)"', html)
        if m:
            first_name = m.group(1)
        m = re.search(r'latestChapterId[:\s]*(\d+)', html)
        if m:
            latest_id = m.group(1)
        m = re.search(r'latestChapterName[:\s]*"([^"]+)"', html)
        if m:
            latest_name = m.group(1)

        first_id = ch_ids[0] if ch_ids else ""
        if first_id:
            chapters.append(Chapter(index=1, title=first_name or "\u7b2c1\u7ae0",
                                     chapter_id=first_id,
                                     url=f"https://m.zongheng.com/chapter/{book_id}/{first_id}.html"))
        if latest_id and latest_id != first_id:
            chapters.append(Chapter(index=999, title=latest_name or "\u6700\u65b0\u7ae0\u8282",
                                     chapter_id=latest_id,
                                     url=f"https://m.zongheng.com/chapter/{book_id}/{latest_id}.html"))
        return chapters

    def fetch_chapter(self, chapter: Chapter) -> str:
        """Fetch chapter content from mobile site via curl_cffi."""
        url = chapter.url
        if not url:
            return ""

        # Ensure mobile URL
        if "www.zongheng.com" in url or "read.zongheng.com" in url:
            url = re.sub(r'(www|read)\.zongheng\.com', 'm.zongheng.com', url)

        try:
            s = self._get_session()
            resp = s.get(url, impersonate="chrome131", timeout=15)
            resp.encoding = "utf-8"
            html = resp.text
        except Exception as e:
            logger.warning("获取章节失败 %s: %s", url, e)
            return ""

        # Extract content from various container patterns
        for pattern in [
            r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="content"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*chapter-content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*read-content[^"]*"[^>]*>(.*?)</div>',
            r'<article[^>]*>(.*?)</article>',
        ]:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                text = re.sub(r'<[^>]+>', '', m.group(1))
                text = re.sub(r'\s*\n\s*', '\n', text).strip()
                if len(text) > 100:
                    text = self._clean_content(text)
                    return text

        # Fallback: extract paragraphs
        paragraphs = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
        texts = [re.sub(r'<[^>]+>', '', p).strip() for p in paragraphs if len(re.sub(r'<[^>]+>', '', p).strip()) > 10]
        if texts:
            return "\n".join(texts)

        return ""

    def _clean_content(self, text: str) -> str:
        text = re.sub(
            r'(?:^|\n)\s*(?:本章完|请记住本书首发域名|一秒记住|天才一秒记住|'
            r'手机用户请浏览|提示：|推荐：|https?://\S+|'
            r'纵横中文网.*?www\.zongheng\.com|快捷键|听书|'
            r'下一章|上一章|返回目录)\s*(?:\n|$)',
            '', text, flags=re.IGNORECASE
        )
        return text.strip()

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        import os
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
