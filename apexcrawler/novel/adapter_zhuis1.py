"""zhuis1.com (追书网) novel site adapter — mobile-only anti-scraping site.

Uses CloakedEngine for JS rendering + mobile UA spoofing to bypass
zhuis1's 4-layer anti-scraping:
  1. Server-side UA detection (mobile-only)
  2. Empty HTML for non-browser clients
  3. JS runtime checks (navigator.platform, maxTouchPoints)
  4. Encrypted Ajax requests with IP frequency limiting

Chapter pagination: long chapters are split across multiple pages
with "下一页" links that must be followed and merged.
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import List, Optional

from apexcrawler.novel.adapter_base import SiteAdapter, BookInfo, Chapter
from apexcrawler.novel.engine import register_adapter

logger = logging.getLogger(__name__)

# 移动端UA（zhuis1仅对移动端返回内容）
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36"
)

# 内容容器CSS选择器（按优先级尝试）
CONTENT_SELECTORS = [
    "#content", ".content", "#chaptercontent", ".chapter-content",
    "#nr", "#text", "#main", ".booktxt", ".chapter-text",
    "article", "main", ".novel-content", ".read-content",
]

# 需要过滤的噪声文本片段
SKIP_PATTERNS = [
    "上一章", "下一章", "查看目录", "查看最新章节",
    "将本站", "收藏本书", "账号", "密码", "注册",
    "追书网", "提示", "书架", "首页", "排行",
    "如果您觉得", "请牢记", "您可以点击", "如果此章",
    "看了《", "本站若有", "侵权", "联系我们",
    "推荐阅读", "作者:", "本书", "下载", "TXT",
    "APP", "安卓", "苹果", "手机", "微信", "关注",
]


@register_adapter
class Zhuis1Adapter(SiteAdapter):
    """Adapter for zhuis1.com (追书网).

    Matches URLs like:
      https://m.zhuis1.com/fs/23515217085/
      https://www.zhuis1.com/fs/23515217085/
    """

    DOMAIN = "zhuis1.com"
    MOBILE_BASE = "https://m.zhuis1.com"
    CHAPTER_LIST_PATTERN = r"/fs/(\d+)/?"
    CHAPTER_URL_PATTERN = r"/fszs/\d+/\d+\.html"

    def __init__(self):
        self._engine = None
        self._base_url = ""

    # ── URL匹配 ──────────────────────────────────────────────────────

    def match(self, url: str) -> bool:
        return self.DOMAIN in url

    def _extract_book_id(self, url: str) -> str:
        m = re.search(self.CHAPTER_LIST_PATTERN, url)
        if m:
            return m.group(1)
        raise ValueError(f"Cannot extract book_id from: {url}")

    @staticmethod
    def _to_mobile_url(url: str) -> str:
        """将任意zhuis1 URL转换为移动端URL"""
        return url.replace("www.zhuis1.com", "m.zhuis1.com").replace(
            "http://", "https://"
        )

    # ── 引擎管理 ──────────────────────────────────────────────────────

    async def _ensure_engine(self):
        """懒初始化CloakedEngine（含移动端模拟+资源拦截）"""
        if self._engine is not None:
            return

        from apexcrawler.engines.cloaked import CloakedEngine

        logger.info("🚀 启动 CloakedEngine (移动端模拟)...")
        eng = CloakedEngine(headless=True, viewport={"width": 390, "height": 844})
        await eng.launch()

        # 移动端UA
        await eng._context.set_extra_http_headers({"User-Agent": MOBILE_UA})

        # JS运行时伪造（绕过第3层反爬）
        await eng._context.add_init_script("""
            Object.defineProperty(navigator, 'platform', { get: () => 'iPhone' });
            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 5 });
            Object.defineProperty(screen, 'width', { get: () => 390 });
            Object.defineProperty(screen, 'height', { get: () => 844 });
            Object.defineProperty(navigator, 'language', { get: () => 'zh-CN' });
            Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en'] });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 4 });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 4 });
            Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
            Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });
        """)

        # 拦截图片/CSS/字体（优化加载速度）
        await eng._page.route("**/*", self._handle_route)

        self._engine = eng
        logger.info("✓ CloakedEngine 初始化完成")

    async def _handle_route(self, route, request):
        """拦截不需要的资源请求"""
        if request.resource_type in ("image", "stylesheet", "font", "media"):
            await route.abort()
        else:
            await route.continue_()

    async def _close_engine(self):
        """关闭引擎"""
        if self._engine:
            await self._engine.close()
            self._engine = None
            logger.info("🛑 CloakedEngine 已关闭")

    # ── 章节列表获取 ──────────────────────────────────────────────────

    def get_book_info(self, url: str) -> BookInfo:
        """同步入口：获取书籍信息和章节列表"""
        return _run_async_safe(self._async_get_book_info(url))

    async def _async_get_book_info(self, url: str) -> BookInfo:
        """异步获取书籍信息和章节列表"""
        await self._ensure_engine()

        book_id = self._extract_book_id(url)
        mobile_url = self._to_mobile_url(url)
        self._base_url = mobile_url

        logger.info("📖 获取章节列表: %s", mobile_url)
        await self._engine._page.goto(mobile_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(random.uniform(2, 4))

        # JS提取章节链接
        js = """
        (() => {
            const links = document.querySelectorAll('a[href*="/fszs/"]');
            const seen = new Set();
            const chapters = [];
            for (const link of links) {
                const href = link.href;
                if (!seen.has(href) && /\\/fszs\\/\\d+\\/\\d+\\.html/.test(href)) {
                    seen.add(href);
                    chapters.push({ url: href, name: link.textContent.trim() });
                }
            }
            return JSON.stringify(chapters);
        })()
        """
        data = json.loads(await self._engine._page.evaluate(js))
        data.reverse()  # 从第一章开始

        # 尝试获取书名
        try:
            title = await self._engine._page.evaluate(
                "document.querySelector('h1')?.textContent?.trim() || ''"
            )
        except Exception:
            title = ""
        if not title:
            title = f"Book {book_id}"

        chapters = []
        for i, ch in enumerate(data, 1):
            chapters.append(Chapter(
                index=i,
                title=ch["name"],
                chapter_id=str(i),
                url=self._to_mobile_url(ch["url"]),
                is_vip=False,
            ))

        logger.info("  ✓ 找到 %d 个章节", len(chapters))
        return BookInfo(
            book_id=book_id,
            title=title,
            chapters=chapters,
        )

    # ── 章节内容获取（含分页处理）──────────────────────────────────────

    def fetch_chapter(self, chapter: Chapter) -> str:
        """同步入口：获取单个章节内容"""
        return _run_async_safe(self._async_fetch_chapter(chapter))

    async def _async_fetch_chapter(self, chapter: Chapter) -> str:
        """异步获取章节内容（自动处理分页）"""
        await self._ensure_engine()

        url = self._to_mobile_url(chapter.url)
        all_content = []
        current_url = url
        page_num = 1

        while current_url:
            # 导航到当前页
            await self._engine._page.goto(current_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(1.5, 3))

            # 提取页面HTML
            html = await self._engine._page.content()
            content = _smart_extract(html)

            if content:
                all_content.append(content)

            # 检测"下一页"链接（分页处理）
            next_page = await self._engine._page.query_selector('a:has-text("下一页")')
            if next_page:
                next_href = await next_page.get_attribute("href")
                if next_href and next_href != "#":
                    if next_href.startswith("http"):
                        current_url = self._to_mobile_url(next_href)
                    else:
                        current_url = self.MOBILE_BASE + next_href
                    page_num += 1
                    logger.debug("    ↳ 检测到第%d页，继续提取...", page_num)
                else:
                    current_url = None
            else:
                current_url = None

        merged = "\n\n".join(all_content) if all_content else ""
        if not merged or len(merged) < 50:
            logger.warning("  ⚠️ 章节内容过短或为空: %s", chapter.title)

        return merged

    # ── 批量下载（含断点续传+智能延迟）─────────────────────────────────

    def download(self, book: BookInfo, chapters: List[Chapter], output: str = "txt") -> str:
        """下载章节并保存到文件，支持断点续传"""
        return _run_async_safe(self._async_download(book, chapters, output))

    async def _async_download(
        self, book: BookInfo, chapters: List[Chapter], output: str = "txt"
    ) -> str:
        await self._ensure_engine()

        total = len(chapters)
        output_dir = os.path.dirname(os.path.abspath(book.book_id)) or "."
        filename = f"{book.title or book.book_id}.{output}"
        filepath = os.path.join(output_dir, filename)
        progress_file = os.path.join(output_dir, f"{book.book_id}_progress.json")

        # 断点续传：加载进度
        downloaded_set = _load_progress(progress_file)

        # 打开输出文件
        mode = "a" if os.path.exists(filepath) and downloaded_set else "w"
        with open(filepath, mode, encoding="utf-8") as f:
            if mode == "w":
                f.write(f"{book.title}\n")
                f.write(f"来源: {self._base_url}\n")
                f.write(f"共{total}章\n")
                f.write(f"下载时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 60 + "\n\n")
                f.flush()

            remaining = [
                (i, ch) for i, ch in enumerate(chapters) if i not in downloaded_set
            ]

            if not remaining:
                logger.info("✅ 所有章节已下载完成！")
                return filepath

            logger.info("📥 开始下载（剩余 %d/%d 章）...", len(remaining), total)

            for idx, (i, ch) in enumerate(remaining):
                # 重试机制（指数退避）
                content = ""
                for attempt in range(1, 4):
                    try:
                        content = await self._async_fetch_chapter(ch)
                        if content and len(content) >= 50:
                            break
                    except Exception as e:
                        if attempt < 3:
                            delay = 2 ** attempt
                            logger.warning(
                                "  ⚠️ [%d/%d] 第%d次失败: %s，%ds后重试...",
                                i + 1, total, attempt, e, delay,
                            )
                            await asyncio.sleep(delay)
                        else:
                            logger.error("  ❌ [%d/%d] 下载失败: %s", i + 1, total, e)
                            content = f"(下载失败: {e})"

                # 写入文件
                if content and len(content.strip()) >= 200:
                    f.write(f"\n{ch.title}\n\n{content}\n\n" + "-" * 60 + "\n\n")
                    f.flush()
                else:
                    logger.info("  ⏭️ 跳过未完成章节 (%d字): %s", len(content.strip()), ch.title)
                    downloaded_set.add(i)  # 标记为已处理，避免下次重试
                    _save_progress(progress_file, downloaded_set)
                    continue

                # 记录进度
                downloaded_set.add(i)
                if idx % 5 == 0:
                    _save_progress(progress_file, downloaded_set)
                    logger.info("  ✓ 进度已保存 (%d/%d)", i + 1, total)

                logger.info("  [%d/%d] %s ✓", i + 1, total, ch.title[:20])

                # 智能延迟（正态分布 + 章节长度因子）
                delay = max(2.0, min(5.0, random.gauss(3.5, 1.0)))
                if len(content) > 2000:
                    delay += random.uniform(1, 3)
                if random.random() < 0.1:
                    delay += random.uniform(10, 30)
                await asyncio.sleep(delay)

                # 每10章暂停（模拟人类休息）
                if (idx + 1) % 10 == 0 and (idx + 1) < len(remaining):
                    logger.info("  💤 已下载%d章，暂停60秒...", idx + 1)
                    await asyncio.sleep(60)

        _save_progress(progress_file, downloaded_set)
        logger.info("\n✅ 下载完成！共%d章", len(downloaded_set))
        logger.info("📁 文件保存在: %s", filepath)

        await self._close_engine()
        return filepath


# ── 智能解析模块 ──────────────────────────────────────────────────────

def _smart_extract(html: str) -> Optional[str]:
    """三层Fallback智能提取正文"""
    # 方法1: trafilatura
    content = _try_trafilatura(html)
    if content:
        return content

    # 方法2: BeautifulSoup
    content = _try_beautifulsoup(html)
    if content:
        return content

    # 方法3: 正则兜底
    return _try_regex(html)


def _try_trafilatura(html: str) -> Optional[str]:
    try:
        import trafilatura
        result = trafilatura.extract(html)
        if result and len(result) > 100:
            return _clean_text(result)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("trafilatura提取失败: %s", e)
    return None


def _try_beautifulsoup(html: str) -> Optional[str]:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        for tag in soup(["script", "style", "iframe", "nav", "footer", "header", "aside"]):
            tag.decompose()

        for selector in CONTENT_SELECTORS:
            elem = soup.select_one(selector)
            if elem:
                text = elem.get_text(separator="\n", strip=True)
                if len(text) > 100:
                    return _clean_text(text)

        # 兜底：最长的div
        divs = soup.find_all("div")
        if divs:
            longest = max(divs, key=lambda d: len(d.get_text()), default=None)
            if longest:
                text = longest.get_text(separator="\n", strip=True)
                if len(text) > 200:
                    return _clean_text(text)
    except ImportError:
        pass
    except Exception as e:
        logger.debug("BeautifulSoup提取失败: %s", e)
    return None


def _try_regex(html: str) -> Optional[str]:
    patterns = [
        r'id=["\']content["\'][^>]*>(.*?)</div>',
        r'class=["\']content["\'][^>]*>(.*?)</div>',
        r'<div[^>]*id=["\'](?:nr|text|main|booktxt)["\'][^>]*>(.*?)</div>',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
        if match:
            content = match.group(1)
            content = re.sub(r"<br\s*/?>", "\n", content)
            content = re.sub(r"<[^>]+>", "", content)
            content = re.sub(r"&nbsp;", " ", content)
            content = re.sub(r"\n{3,}", "\n\n", content)
            cleaned = _clean_text(content)
            if len(cleaned) > 100:
                return cleaned
    return None


def _clean_text(text: str) -> str:
    """清洗文本：去除广告/导航等噪声"""
    if not text:
        return ""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if any(p in line for p in SKIP_PATTERNS):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


# ── 进度管理 ──────────────────────────────────────────────────────────

def _load_progress(progress_file: str) -> set:
    """加载下载进度（返回已下载章节索引集合）"""
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("downloaded", []))
        except Exception as e:
            logger.warning("加载进度失败: %s", e)
    return set()


def _save_progress(progress_file: str, downloaded: set):
    """保存下载进度"""
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(
            {
                "downloaded": list(downloaded),
                "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            f,
            ensure_ascii=False,
            indent=2,
        )


# ── 异步工具函数 ──────────────────────────────────────────────────────

def _run_async_safe(coro):
    """安全地从同步上下文运行协程（兼容已有事件循环的场景）"""
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
