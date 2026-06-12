"""Zongheng (纵横中文网) novel site adapter — traditional server-rendered pages."""
from __future__ import annotations
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

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


@register_adapter
class ZonghengAdapter(SiteAdapter):
    """Adapter for zongheng.com — Baidu's traditional novel platform.

    URL patterns:
      Book info (main):  https://www.zongheng.com/detail/{book_id}
      Book info (female): https://huayu.zongheng.com/book/{book_id}.html
      Chapter:           https://www.zongheng.com/chapter/{book_id}/{chapter_id}.html
    """

    URL_PATTERNS = [
        r"(?:www\.)?zongheng\.com/detail/(\d+)",
        r"(?:www\.)?zongheng\.com/chapter/(\d+)/(\d+)",
        r"huayu\.zongheng\.com/book/(\d+)",
    ]

    def __init__(self):
        self._session = None

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
                "Referer": "https://www.zongheng.com/",
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

        # Build the correct detail page URL
        if "huayu.zongheng.com" in url:
            detail_url = f"https://huayu.zongheng.com/book/{book_id}.html"
        else:
            detail_url = f"https://www.zongheng.com/detail/{book_id}"

        resp = self.session.get(detail_url, timeout=15)
        resp.encoding = "utf-8"
        html = resp.text

        # Extract book title from <title> tag
        title_match = re.search(r'<title>(.*?)最新章节.*?</title>', html)
        if not title_match:
            title_match = re.search(r'<title>(.*?)</title>', html)
        title = title_match.group(1).strip() if title_match else f"Book {book_id}"

        # Extract author from NUXT data
        author_match = re.search(r'"author_name"\s*:\s*"([^"]+)"', html)
        if not author_match:
            author_match = re.search(r'作者[：:]\s*([^<\n&]{2,20})', html)
        author = author_match.group(1).strip() if author_match else ""

        # Extract chapter list — try API first, fall back to NUXT data, then HTML
        chapters = self._fetch_chapters_via_api(book_id)
        if not chapters:
            chapters = self._extract_chapters_from_nuxt(html, book_id)
        if not chapters:
            if BeautifulSoup is None:
                raise ImportError("Missing dependency: beautifulsoup4 → pip install beautifulsoup4")
            soup = BeautifulSoup(html, "html.parser")
            chapters = self._extract_chapters(soup, book_id, detail_url)

        return BookInfo(
            book_id=book_id,
            title=title,
            author=author,
            chapters=chapters,
        )

    def _fetch_chapters_via_api(self, book_id: str) -> List[Chapter]:
        """Fetch chapter list from Zongheng's chapterList API."""
        chapters = []
        try:
            resp = self.session.get(
                f"https://www.zongheng.com/book/{book_id}/chapterList",
                timeout=15,
                headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"https://www.zongheng.com/detail/{book_id}"},
            )
            data = resp.json()

            # The API returns chapter data, try different formats
            chapter_data = data.get("data", data)
            if isinstance(chapter_data, dict):
                chapter_list = chapter_data.get("chapterList", chapter_data.get("list", []))
            elif isinstance(chapter_data, list):
                chapter_list = chapter_data
            else:
                chapter_list = []

            for i, ch in enumerate(chapter_list, 1):
                ch_title = ch.get("chapterName", "") or ch.get("chapterTitle", "") or ch.get("title", f"第{i}章")
                ch_id = str(ch.get("chapterId", ch.get("id", i)))
                ch_url = f"https://www.zongheng.com/chapter/{book_id}/{ch_id}.html"

                chapters.append(Chapter(
                    index=i,
                    title=ch_title,
                    chapter_id=ch_id,
                    url=ch_url,
                ))
        except Exception as e:
            logger.warning("Zongheng API chapter list failed: %s", e)

        return chapters

    def _extract_chapters_from_nuxt(self, html: str, book_id: str) -> List[Chapter]:
        """Extract chapter list from __NUXT__ embedded data."""
        chapters = []
        import json

        # Extract NUXT data
        nuxt_match = re.search(r'window\.__NUXT__\s*=\s*(\{.*?\});', html, re.DOTALL)
        if not nuxt_match:
            return chapters

        try:
            # Clean up the NUXT data so it's valid JSON (remove trailing commas, function calls)
            raw = nuxt_match.group(1)
            # Remove trailing function-like parameters like: ,"15","4","\u002F"));
            raw = re.sub(r'\)\s*\)\s*;\s*$', ')', raw)
            data = json.loads(raw)

            # Navigate to find chapter list - Zongheng NUXT structure varies
            # Try common paths
            state = data.get("state", data)
            books = state.get("books", {})
            book_info = books.get("booksInfo", {})

            if book_id in book_info:
                info = book_info[book_id]
                chapter_data = info.get("chapterList", info.get("chapters", []))
            else:
                # Try deeper search
                chapter_data = self._find_chapters_in_nuxt(state)

            for i, ch in enumerate(chapter_data, 1):
                if isinstance(ch, dict):
                    ch_title = ch.get("chapterName", ch.get("title", f"第{i}章"))
                    ch_id = str(ch.get("chapterId", ch.get("id", i)))
                elif isinstance(ch, str):
                    # String array format
                    ch_title = ch
                    ch_id = str(i)
                else:
                    continue

                chapters.append(Chapter(
                    index=i,
                    title=ch_title,
                    chapter_id=ch_id,
                    url=f"https://www.zongheng.com/chapter/{book_id}/{ch_id}.html",
                ))

        except Exception as e:
            logger.warning("NUXT parse failed: %s", e)

        return chapters

    def _find_chapters_in_nuxt(self, obj, depth=0):
        """Recursively search for chapter arrays in NUXT data."""
        if depth > 5:
            return []
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key.lower() in ("chapterlist", "chapters", "chapter_list", "catalog"):
                    if isinstance(val, list) and len(val) > 3:
                        return val
                result = self._find_chapters_in_nuxt(val, depth + 1)
                if result:
                    return result
        elif isinstance(obj, list):
            for item in obj:
                result = self._find_chapters_in_nuxt(item, depth + 1)
                if result:
                    return result
        return []

    def _extract_chapters(self, soup, book_id: str, detail_url: str) -> List[Chapter]:
        """HTML parsing fallback for chapter extraction."""
        chapters = []
        seen_hrefs = set()

        # Try multiple common selectors for chapter list
        selectors = [
            ".chapter-list a", "#chapter-list a", ".chapters a",
            ".chapterlist a", ".chapter-item a", ".volume a",
            ".book-chapter-list a", "[class*='chapter'] a",
            ".catalog-list a", ".directory a", ".chapter-content a",
            "ul.chapter li a", ".chapter-box a",
        ]

        chapter_links = []
        for selector in selectors:
            links = soup.select(selector)
            if len(links) > 3:
                chapter_links = links
                break

        # Fallback: find all links containing "chapter" in href
        if not chapter_links or len(chapter_links) < 3:
            chapter_links = soup.select(f'a[href*="chapter"]')
            if not chapter_links or len(chapter_links) < 3:
                # Look for links with number.html pattern
                chapter_links = soup.select(f'a[href*="/{book_id}/"]')
                if not chapter_links or len(chapter_links) < 3:
                    chapter_links = soup.select('a[href*=".html"]')

        for i, a in enumerate(chapter_links, 1):
            href = a.get("href", "")
            if not href or href in seen_hrefs or href.startswith("#"):
                continue
            seen_hrefs.add(href)

            # Make absolute URL
            if href.startswith("//"):
                href = "https:" + href
            elif href.startswith("/"):
                href = "https://www.zongheng.com" + href
            elif not href.startswith("http"):
                continue

            title = a.get_text(strip=True) or f"第{i}章"
            if not title:
                continue

            # Extract chapter_id
            ch_id_match = re.search(r'/chapter/\d+/(\d+)', href)
            ch_id = ch_id_match.group(1) if ch_id_match else str(i)

            chapters.append(Chapter(
                index=i,
                title=title,
                chapter_id=ch_id,
                url=href,
                is_vip=False,
            ))

        # If still no chapters, try parsing the page for JS-embedded chapter data
        if not chapters:
            chapters = self._extract_chapters_from_js(soup, resp.text if hasattr(self, '_last_html') else "")

        logger.info("纵横中文网章节列表: %d 章", len(chapters))
        return chapters

    def _extract_chapters_from_js(self, soup, html: str) -> List[Chapter]:
        """Extract chapter data from embedded JavaScript if available."""
        chapters = []
        if not html:
            return chapters

        # Look for chapter array in JavaScript
        js_patterns = [
            r'chapterList\s*=\s*(\[.*?\])\s*;',
            r'"chapterList"\s*:\s*(\[.*?\])',
            r'chapters\s*:\s*(\[.*?\])',
        ]

        import json
        for pattern in js_patterns:
            m = re.search(pattern, html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1))
                    for i, ch in enumerate(data, 1):
                        ch_title = ch.get("title", "") or ch.get("chapterName", "") or f"第{i}章"
                        ch_id = ch.get("id", "") or ch.get("chapterId", "") or str(i)
                        ch_url = ch.get("url", "") or f"https://www.zongheng.com/chapter/{ch.get('bookId','')}/{ch_id}.html"

                        chapters.append(Chapter(
                            index=i,
                            title=ch_title,
                            chapter_id=str(ch_id),
                            url=ch_url,
                        ))
                    if chapters:
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

        return chapters

    def fetch_chapter(self, chapter: Chapter) -> str:
        url = chapter.url
        if not url:
            return ""

        try:
            resp = self.session.get(url, timeout=15)
            resp.encoding = "utf-8"
        except Exception as e:
            logger.warning("获取章节失败 %s: %s", url, e)
            return ""

        if BeautifulSoup is None:
            raise ImportError("Missing dependency: beautifulsoup4 → pip install beautifulsoup4")
        soup = BeautifulSoup(resp.text, "html.parser")

        # Try multiple content selectors
        content_selectors = [
            ".content", "#content", ".read-content", ".chapter-content",
            ".text", ".article-content", "#chapter-content",
            ".book-content", ".main-text", ".chapter-text",
            '[class*="content"]', '[class*="text"]',
            "article", ".article",
        ]

        for selector in content_selectors:
            el = soup.select_one(selector)
            if el:
                text = el.get_text("\n", strip=True)
                if len(text) > 100:
                    # Clean up common noise
                    text = re.sub(
                        r'(?:^|\n)\s*(?:本章完|请记住本书首发域名|一秒记住|天才一秒记住|'
                        r'手机用户请浏览|提示：|推荐：|https?://\S+|'
                        r'纵横中文网.*?www\.zongheng\.com)\s*(?:\n|$)',
                        '', text
                    )
                    return text.strip()

        # Fallback: extract all substantial paragraphs
        paragraphs = soup.select("p")
        texts = [p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 10]
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
            content_lines.append(f"\n\n第{ch.index}章 {ch.title}\n\n{text}\n")
            logger.info("下载进度: %d/%d (%.0f%%)", i + 1, total, (i + 1) / total * 100)

        path = os.path.join(os.getcwd(), filename)
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(content_lines))
        logger.info("下载完成: %s (%d 章, %s)", path, total, output.upper())
        return path
