"""Fanqie Novel (番茄小说) adapter — free reading platform by ByteDance."""
from __future__ import annotations
import asyncio
import json
import logging
import re
import time
from typing import List, Optional
from apexcrawler.novel.adapter_base import SiteAdapter, BookInfo, Chapter
from apexcrawler.novel.engine import register_adapter

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    requests = None


@register_adapter
class FanqieAdapter(SiteAdapter):
    """Adapter for fanqienovel.com — ByteDance's free novel platform.

    URL patterns:
      Book info:  https://fanqienovel.com/page/{book_id}
      Chapter:    https://fanqienovel.com/reader/{chapter_id}
      API:        https://novel.snssdk.com/api/novel/reader/full/v1/
    """

    URL_PATTERNS = [
        r"(?:www\.)?fanqienovel\.com/page/(\d+)",
        r"(?:www\.)?fanqienovel\.com/reader/(\d+)",
    ]

    BOOK_API = "https://novel.snssdk.com/api/novel/book/detail/v1/"
    CHAPTER_API = "https://novel.snssdk.com/api/novel/reader/full/v1/"

    def __init__(self):
        self._session = None

    @property
    def session(self):
        if self._session is None:
            if requests is None:
                raise ImportError("Missing dependency: requests -> pip install requests")
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G998B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Origin": "https://fanqienovel.com",
                "Referer": "https://fanqienovel.com/",
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
        try:
            resp = self.session.get(self.BOOK_API, params={"book_id": book_id}, timeout=15)
            data = resp.json()
            book_data = data.get("data", {})
            title = book_data.get("book_name", "") or book_data.get("title", f"Book {book_id}")
            author = book_data.get("author", "") or book_data.get("author_name", "")
            chapters = self._fetch_chapters_via_api(book_id)
            return BookInfo(book_id=book_id, title=title, author=author, chapters=chapters)
        except Exception as e:
            logger.warning("API fetch failed for %s: %s, falling back to HTML", book_id, e)
            return self._get_book_info_via_html(book_id)

    def _get_book_info_via_html(self, book_id: str) -> BookInfo:
        url = f"https://fanqienovel.com/page/{book_id}"
        resp = self.session.get(url, timeout=15)
        resp.encoding = "utf-8"
        html = resp.text

        title = ""
        m = re.search(r'"book_name"\s*:\s*"([^"]+)"', html)
        if m:
            title = m.group(1)
        else:
            m = re.search(r'<title>(.*?)</title>', html)
            if m:
                raw = m.group(1).split("完整版")[0].split("_")[0].split("-")[0].strip()
                title = raw if raw else f"Book {book_id}"

        author = ""
        for p in [r'"author"\s*:\s*"([^"]+)"', r'"author_name"\s*:\s*"([^"]+)"']:
            m = re.search(p, html)
            if m:
                author = m.group(1)
                break

        chapters = self._fetch_chapters_via_html(book_id, html)
        return BookInfo(book_id=book_id, title=title, author=author, chapters=chapters)

    def _fetch_chapters_via_api(self, book_id: str) -> List[Chapter]:
        chapters = []
        try:
            resp = self.session.get(
                "https://novel.snssdk.com/api/novel/book/directory/v1/",
                params={"book_id": book_id, "page_num": 0, "page_size": 100},
                timeout=15,
            )
            data = resp.json()
            if "data" in data and "chapter_list" in data["data"]:
                for i, ch in enumerate(data["data"]["chapter_list"], 1):
                    chapters.append(Chapter(
                        index=i,
                        title=ch.get("chapter_title", "") or ch.get("title", f"\u7b2c{i}\u7ae0"),
                        chapter_id=str(ch.get("chapter_id", "")),
                        url=f"https://fanqienovel.com/reader/{ch.get('chapter_id', '')}",
                        is_vip=ch.get("is_vip", 0) == 1,
                        word_count=int(ch.get("word_count", 0)),
                    ))
        except Exception as e:
            logger.warning("API chapter list failed: %s", e)

        if not chapters:
            return self._fetch_chapters_via_html(book_id)
        return chapters

    def _fetch_chapters_via_html(self, book_id: str, html: str = "") -> List[Chapter]:
        if not html:
            resp = self.session.get(f"https://fanqienovel.com/page/{book_id}", timeout=15)
            resp.encoding = "utf-8"
            html = resp.text

        chapters = []
        for pattern in [r'"chapter_list"\s*:\s*(\[.*?\])', r'"chapters"\s*:\s*(\[.*?\])', r'chapterList\s*=\s*(\[.*?\])']:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    for i, ch in enumerate(data, 1):
                        ch_id = ch.get("chapter_id", "") or ch.get("id", "")
                        ch_title = ch.get("chapter_title", "") or ch.get("title", f"\u7b2c{i}\u7ae0")
                        chapters.append(Chapter(index=i, title=ch_title, chapter_id=str(ch_id), url=f"https://fanqienovel.com/reader/{ch_id}"))
                    if chapters:
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

        if not chapters:
            links = re.findall(r'href="(/reader/(\d+))"[^>]*>(.*?)<', html)
            for i, (path, ch_id, t) in enumerate(links, 1):
                chapters.append(Chapter(index=i, title=t.strip() or f"\u7b2c{i}\u7ae0", chapter_id=ch_id, url=f"https://fanqienovel.com{path}"))

        chapters.reverse()
        for i, ch in enumerate(chapters, 1):
            ch.index = i
        return chapters

    def fetch_chapter(self, chapter: Chapter) -> str:
        chapter_id = chapter.chapter_id
        if not chapter_id:
            m = re.search(r'/reader/(\d+)', chapter.url)
            chapter_id = m.group(1) if m else ""
        if not chapter_id:
            return ""

        text = self._fetch_via_html(chapter_id)
        if text:
            return text
        return self._fetch_via_api(chapter_id)

    def _fetch_via_api(self, chapter_id: str) -> str:
        try:
            resp = self.session.get(self.CHAPTER_API, params={"item_id": chapter_id}, timeout=15)
            data = resp.json()
            cdata = data.get("data", {}) or data.get("content", {})
            content = cdata.get("content", "") if isinstance(cdata, dict) else (cdata if isinstance(cdata, str) else "")
            if content:
                if '\\u' in content:
                    try:
                        content = json.loads('"' + content + '"')
                    except Exception:
                        pass
                content = re.sub(r'<[^>]+>', '', content).strip()
                if len(content) > 50:
                    return self._font_decode(content)
            return ""
        except Exception:
            return ""

    def _font_decode(self, text: str) -> str:
        """Apply font decoder to handle custom font anti-captcha characters."""
        try:
            from apexcrawler.novel.font_decoder import FontDecoder
            decoder = FontDecoder()
            decoded = decoder.decode_html(text)
            return decoded if decoded and decoded != text else text
        except Exception:
            return text

    def _fetch_via_html(self, chapter_id: str) -> str:
        """Parse chapter from HTML reader page with font anti-captcha decoding."""
        url = f"https://fanqienovel.com/reader/{chapter_id}"
        try:
            resp = self.session.get(url, timeout=15)
            resp.encoding = "utf-8"
            html = resp.text

            # Step 1: Apply FontDecoder on the FULL HTML page
            # (needs @font-face CSS to extract custom font and map PUA chars)
            try:
                from apexcrawler.novel.font_decoder import FontDecoder
                decoder = FontDecoder()
                decoded_html = decoder.decode_html(html)
                if decoded_html and decoded_html != html:
                    html = decoded_html
            except Exception:
                pass

            # Step 2: Extract raw content from JSON data in (now decoded) HTML
            raw = ""
            for p in [r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', r'"content"\s*:\s*"((?:[^"]|\\")*)"']:
                m = re.search(p, html, re.DOTALL)
                if m:
                    raw = m.group(1)
                    break

            if not raw:
                return ""

            # Step 3: Decode unicode-escaped HTML entities using proper JSON decoding
            if '\\u' in raw:
                try:
                    # Use json decoder for proper unicode escape handling
                    raw = json.loads('"' + raw + '"')
                except Exception:
                    try:
                        raw = raw.encode().decode('unicode-escape')
                    except Exception:
                        pass

            # Step 4: Strip HTML tags and normalize whitespace
            text = re.sub(r'<[^>]+>', '', raw)
            text = re.sub(r'\s*\n\s*', '\n', text).strip()

            if len(text) < 50:
                return ""

            return text

        except Exception as e:
            logger.warning("HTML chapter fetch failed for %s: %s", chapter_id, e)
            return ""

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        import os
        filename = f"{book.title or book.book_id}_{int(time.time())}.{output}"
        content_lines = []
        total = len(chapters)
        for i, ch in enumerate(chapters):
            text = self.fetch_chapter(ch)
            content_lines.append(f"\n\n\u7b2c{ch.index}\u7ae0 {ch.title}\n\n{text}\n")
            logger.info("\u4e0b\u8f7d\u8fdb\u5ea6: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)
        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(content_lines))
        logger.info("\u4e0b\u8f7d\u5b8c\u6210: %s (%d \u7ae0, %s)", path, total, output.upper())
        return path
