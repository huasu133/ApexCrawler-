"""
QidianEngine — 起点中文网专用爬取引擎。

集成 WAF 绕过、字体反爬破解、扫码登录、Cookie 管理、
章节列表获取和正文提取等功能。

使用 Playwright 有头浏览器过腾讯云 WAF，curl_cffi 做批量数据通道。

这是一个独立工具引擎，不继承 ApexCrawler 的 BaseEngine 体系。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import re
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from bs4 import BeautifulSoup, Tag as BsTag
except ImportError:
    BeautifulSoup = None  # type: ignore[assignment]
    BsTag = None  # type: ignore[assignment]

try:
    from playwright.async_api import Browser, BrowserContext, Page, async_playwright
except ImportError:
    Browser = None  # type: ignore[assignment]
    BrowserContext = None  # type: ignore[assignment]
    Page = None  # type: ignore[assignment]
    async_playwright = None  # type: ignore[assignment]

try:
    from curl_cffi.requests import Session as CurlSession
except ImportError:
    CurlSession = None  # type: ignore[assignment]

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
# 数据模型
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Chapter:
    """章节数据模型。"""

    chapter_id: int
    book_id: int
    title: str = ""
    index: int = 0
    is_vip: bool = False
    content: str = ""
    word_count: int = 0
    url: str = ""
    fetched_at: Optional[datetime] = None

    def __post_init__(self):
        if self.fetched_at is None:
            self.fetched_at = datetime.now()


@dataclass
class QidianCookie:
    """起点 Cookie 数据模型。"""

    raw: str = ""
    parsed: Dict[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.now() > self.expires_at


@dataclass
class CatalogCache:
    """目录缓存数据模型。"""

    book_id: int
    chapters: List[Chapter] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=datetime.now)
    ttl_seconds: int = 3600

    @property
    def is_stale(self) -> bool:
        elapsed = (datetime.now() - self.fetched_at).total_seconds()
        return elapsed > self.ttl_seconds


@dataclass
class FontMapping:
    """字体映射数据模型。"""

    font_family: str
    char_map: Dict[str, str] = field(default_factory=dict)
    source_url: str = ""
    fetched_at: datetime = field(default_factory=datetime.now)


# ══════════════════════════════════════════════════════════════════════
# QidianEngine
# ══════════════════════════════════════════════════════════════════════


class QidianEngine:
    """
    起点中文网专用爬取引擎。

    使用方法::

        engine = QidianEngine()
        # 1) 扫码登录
        cookies = engine.login_sync()

        # 2) 获取章节列表
        chapters = engine.fetch_catalog(107580)

        # 3) 提取正文
        engine.fetch_chapters(chapters)
    """

    # 起点 URL 常量
    QIDIAN_URL = "https://www.qidian.com"
    CATEGORY_API = "https://book.qidian.com/ajax/book/category"
    CHAPTER_URL_TEMPLATE = "https://vipreader.qidian.com/chapter/{book_id}/{chapter_id}"

    # WAF 挑战参数
    WAF_CHALLENGE_TIMEOUT_MS = 35_000
    WAF_POLL_INTERVAL_MS = 500

    # 请求头（模拟 Chrome 131 on Windows）
    DEFAULT_HEADERS: Dict[str, str] = {
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-Ch-Ua": (
            '"Google Chrome";v="131", "Chromium";v="131", "Not=A?Brand";v="24"'
        ),
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }

    def __init__(
        self,
        headless: bool = False,
        proxy: Optional[str] = None,
        retry_times: int = 3,
        retry_delay: float = 2.0,
        timeout: int = 30,
        cookie_dir: Optional[str] = None,
        font_cache_dir: Optional[str] = None,
        storage_dir: Optional[str] = None,
    ):
        """
        初始化 QidianEngine。

        Args:
            headless: 是否使用 Playwright 无头模式（过 WAF 需要真实浏览器）
            proxy: 代理地址，如 "http://127.0.0.1:7890"
            retry_times: HTTP 请求重试次数
            retry_delay: 重试间隔秒数
            timeout: 请求超时秒数
            cookie_dir: Cookie 存储目录（默认 ~/.apexcrawler/cookies）
            font_cache_dir: 字体文件缓存目录
            storage_dir: 爬取结果存储目录
        """
        self.headless = headless
        self.proxy = proxy
        self.retry_times = retry_times
        self.retry_delay = retry_delay
        self.timeout = timeout

        # 目录初始化
        base_dir = Path.home() / ".apexcrawler"
        self._cookie_dir = Path(cookie_dir or base_dir / "qidian" / "cookies")
        self._cookie_dir.mkdir(parents=True, exist_ok=True)
        self._font_cache_dir = Path(font_cache_dir or base_dir / "qidian" / "fonts")
        self._font_cache_dir.mkdir(parents=True, exist_ok=True)
        self._storage_dir = Path(storage_dir or base_dir / "qidian" / "output")
        self._storage_dir.mkdir(parents=True, exist_ok=True)

        # 运行时状态
        self._curl_session: Optional[CurlSession] = None
        self._playwright_browser: Optional[Any] = None
        self._playwright_context: Optional[Any] = None
        self._playwright: Optional[Any] = None
        self._catalog_cache: Dict[int, CatalogCache] = {}

    # ══════════════════════════════════════════════════════════════════
    # 公开方法
    # ══════════════════════════════════════════════════════════════════

    # ── 登录 ──────────────────────────────────────────────────────────

    def login_sync(self) -> QidianCookie:
        """
        同步方式：启动有头浏览器，引导用户扫码登录。

        返回导出的 Cookie 对象，可用于后续请求。
        """
        return asyncio.run(self.login_async())

    async def login_async(self) -> QidianCookie:
        """
        异步方式：启动有头浏览器，引导用户扫码登录。
        """
        if async_playwright is None:
            raise ImportError("playwright 未安装，请执行: pip install playwright && playwright install chromium")

        async with async_playwright() as p:
            launch_opts: Dict[str, Any] = {
                "headless": False,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            }
            if self.proxy:
                launch_opts["proxy"] = {"server": self.proxy}

            browser = await p.chromium.launch(**launch_opts)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=self.DEFAULT_HEADERS["User-Agent"],
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = await context.new_page()

            logger.info("正在打开起点首页: %s", self.QIDIAN_URL)
            await page.goto(self.QIDIAN_URL, wait_until="domcontentloaded")

            # 尝试点击登录按钮
            try:
                login_btn = page.locator("a[href*='passport.qidian.com']")
                if await login_btn.count() > 0:
                    await login_btn.first.click()
                    logger.info("已点击登录按钮")
                else:
                    logger.info("未找到登录按钮，可能已在登录状态")
            except Exception as exc:
                logger.warning("点击登录按钮失败: %s", exc)

            # 等待用户扫码（最多 2 分钟）
            logger.info("请扫描二维码登录（等待 120 秒）...")
            try:
                await page.wait_for_url("**/book/**", timeout=120_000)
            except Exception:
                logger.warning("扫码等待超时，将导出当前已有 Cookie")

            cookies = await context.cookies()
            raw_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

            await browser.close()

            qc = QidianCookie(
                raw=raw_str,
                parsed={c["name"]: c["value"] for c in cookies},
            )
            expires_list = [c.get("expires", 0) for c in cookies if c.get("expires")]
            if expires_list:
                min_expires = min(expires_list)
                if min_expires > 0:
                    qc.expires_at = datetime.fromtimestamp(min_expires)

            logger.info("登录成功，共获取 %d 个 Cookie", len(cookies))
            return qc

    # ── WAF 绕过 ──────────────────────────────────────────────────────

    def bypass_waf_sync(self, url: Optional[str] = None) -> List[Dict[str, str]]:
        """
        同步方式：启动有头浏览器过 WAF，返回已验证的 Cookie 列表。
        """
        return asyncio.run(self.bypass_waf_async(url))

    async def bypass_waf_async(
        self, url: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        异步方式：启动有头浏览器过 WAF，返回已验证的 Cookie 列表。

        Args:
            url: 目标 URL（默认起点首页）

        Returns:
            Playwright cookie 列表，每项含 name, value, domain, path 等字段
        """
        if async_playwright is None:
            raise ImportError(
                "playwright 未安装，请执行: pip install playwright && playwright install chromium"
            )

        target = url or self.QIDIAN_URL

        self._playwright = await async_playwright().start()

        launch_opts: Dict[str, Any] = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        }
        if self.proxy:
            launch_opts["proxy"] = {"server": self.proxy}

        self._playwright_browser = await self._playwright.chromium.launch(
            **launch_opts
        )
        self._playwright_context = await self._playwright_browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=self.DEFAULT_HEADERS["User-Agent"],
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            permissions=[],
        )

        page = await self._playwright_context.new_page()

        try:
            # 注入反检测 JS
            await self._inject_stealth_js(page)

            # 导航
            logger.info("导航到: %s", target)
            await page.goto(target, wait_until="domcontentloaded", timeout=30_000)

            # 等待 WAF 通过
            await self._wait_for_waf_pass(page)

            # 获取 Cookie
            cookies = await self._playwright_context.cookies()
            logger.info("获取到 %d 个 Cookie", len(cookies))
            return cookies
        finally:
            await page.close()

    # ── Cookie 管理 ───────────────────────────────────────────────────

    def save_cookies(self, cookies: List[Dict[str, str]], name: str = "qidian") -> str:
        """保存 Cookie 到本地文件（明文 JSON）。"""
        path = self._cookie_dir / f"{name}.json"
        data = {
            "domain": "qidian.com",
            "saved_at": time.time(),
            "cookies": cookies,
        }
        fd, tmp = tempfile.mkstemp(dir=str(self._cookie_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception:
            os.unlink(tmp)
            raise
        logger.info("Cookie 已保存: %s (%d 个)", path, len(cookies))
        return str(path)

    def load_cookies(self, name: str = "qidian") -> Optional[List[Dict[str, str]]]:
        """加载本地保存的 Cookie。"""
        path = self._cookie_dir / f"{name}.json"
        if not path.exists():
            return None
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        age = time.time() - data.get("saved_at", 0)
        if age > 86400:  # 24 小时过期
            path.unlink()
            logger.info("Cookie 已过期，已删除: %s", path)
            return None
        cookies = data.get("cookies", [])
        logger.info("Cookie 已加载: %s (%d 个)", path, len(cookies))
        return cookies

    def delete_cookies(self, name: str = "qidian") -> None:
        """删除本地保存的 Cookie 文件。"""
        path = self._cookie_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            logger.info("Cookie 已删除: %s", path)

    # ── 章节列表 ──────────────────────────────────────────────────────

    def fetch_catalog(
        self, book_id: int, force_refresh: bool = False
    ) -> List[Chapter]:
        """
        获取指定书籍的章节列表。

        Args:
            book_id: 起点书籍 ID
            force_refresh: 是否强制刷新缓存

        Returns:
            有序的 Chapter 列表
        """
        # 检查缓存
        if not force_refresh and book_id in self._catalog_cache:
            cached = self._catalog_cache[book_id]
            if not cached.is_stale:
                logger.debug("使用缓存章节列表 (book_id=%d)", book_id)
                return cached.chapters

        session = self._get_curl_session()

        logger.info("正在获取章节列表 (book_id=%d)", book_id)
        params = {"_csrfToken": "", "bookId": str(book_id)}
        resp = self._curl_get(session, self.CATEGORY_API, params=params)

        if resp["status_code"] != 200:
            logger.error("获取目录失败: HTTP %d", resp["status_code"])
            return []

        data = resp.get("json") or {}
        chapters: List[Chapter] = []
        index = 0

        vs = data.get("data", {}).get("vs", [])
        for volume in vs:
            cs = volume.get("cs", [])
            for ch in cs:
                index += 1
                chapter = Chapter(
                    chapter_id=int(ch.get("id", 0)),
                    book_id=book_id,
                    title=ch.get("cN", ""),
                    index=index,
                    is_vip=ch.get("vip", 0) == 1,
                    word_count=int(ch.get("cnt", 0)),
                    url=f"https://vipreader.qidian.com/chapter/{book_id}/{ch.get('id', 0)}",
                )
                chapters.append(chapter)

        self._catalog_cache[book_id] = CatalogCache(
            book_id=book_id, chapters=chapters
        )

        logger.info("获取完成: book_id=%d, 共 %d 章", book_id, len(chapters))
        return chapters

    # ── 正文提取 ──────────────────────────────────────────────────────

    def fetch_chapter(self, chapter_info: Chapter) -> Chapter:
        """
        获取单个章节的正文内容。

        Args:
            chapter_info: 章节信息对象

        Returns:
            填充了正文的 Chapter 对象
        """
        session = self._get_curl_session()
        url = chapter_info.url or self.CHAPTER_URL_TEMPLATE.format(
            book_id=chapter_info.book_id, chapter_id=chapter_info.chapter_id
        )

        logger.info("正在获取章节: %s - %s", chapter_info.title, url)
        resp = self._curl_get(session, url)

        if resp["status_code"] != 200:
            logger.error(
                "获取章节失败: HTTP %d (chapter_id=%d)",
                resp["status_code"],
                chapter_info.chapter_id,
            )
            return chapter_info

        html = resp.get("text", "")
        if not html:
            logger.warning("章节页 HTML 为空 (chapter_id=%d)", chapter_info.chapter_id)
            return chapter_info

        # 提取并解码正文
        text, metadata = self._extract_full(html)
        chapter_info.content = text
        if metadata.get("title"):
            chapter_info.title = metadata["title"]
        chapter_info.fetched_at = datetime.now()

        # 字数统计
        if not chapter_info.word_count and text:
            chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
            chapter_info.word_count = chinese_chars

        return chapter_info

    def fetch_chapters(
        self, chapters: List[Chapter], max_workers: int = 1
    ) -> List[Chapter]:
        """
        批量获取章节正文（建议串行，加入延迟）。

        Args:
            chapters: Chapter 列表
            max_workers: 并发数（建议保持 1）

        Returns:
            填充了正文的 Chapter 列表
        """
        results = []
        for i, chapter in enumerate(chapters):
            if chapter.is_vip:
                logger.info("跳过付费章节: %s", chapter.title)
                results.append(chapter)
                continue

            if i > 0 and max_workers == 1:
                delay = 2 + (i % 3)
                time.sleep(delay)

            result = self.fetch_chapter(chapter)
            results.append(result)

            logger.info(
                "进度: %d/%d (%.1f%%)",
                i + 1,
                len(chapters),
                (i + 1) / len(chapters) * 100,
            )

        return results

    # ── 阅读行为模拟 ──────────────────────────────────────────────────

    def simulate_read_delay(self, word_count: int) -> None:
        """模拟阅读章节的时间消耗。"""
        base_delay = random.uniform(3, 8)
        read_time = (word_count / 1000) * 60
        total_delay = base_delay + read_time * random.uniform(0.5, 1.5)
        segments = max(1, int(total_delay / 15))
        seg_time = total_delay / segments
        for _ in range(segments):
            time.sleep(seg_time)
            if random.random() < 0.1:
                time.sleep(random.uniform(2, 8))

    def simulate_inter_chapter_delay(self) -> None:
        """模拟章节之间的等待。"""
        delay = random.uniform(1.5, 5.0)
        if random.random() < 0.2:
            delay += random.uniform(3, 15)
        time.sleep(delay)

    # ── 结果存储 ──────────────────────────────────────────────────────

    def save_book_json(
        self,
        book_id: int,
        book_title: str,
        chapters: List[Chapter],
    ) -> str:
        """将爬取结果保存为 JSON 文件。"""
        data = {
            "book_id": book_id,
            "book_title": book_title,
            "total_chapters": len(chapters),
            "fetched_at": datetime.now().isoformat(),
            "chapters": [
                {
                    "id": ch.chapter_id,
                    "index": ch.index,
                    "title": ch.title,
                    "is_vip": ch.is_vip,
                    "word_count": ch.word_count,
                    "content": ch.content,
                }
                for ch in chapters
            ],
        }
        path = self._storage_dir / f"book_{book_id}.json"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        logger.info("结果已保存: %s", path)
        return str(path)

    def save_book_txt(self, book_id: int, book_title: str, chapters: List[Chapter]) -> str:
        """将爬取结果保存为 TXT 文件。"""
        lines = [f"《{book_title}》", "=" * 40, ""]
        for ch in chapters:
            if not ch.content:
                continue
            lines.append(ch.title)
            lines.append("-" * 20)
            lines.append(ch.content)
            lines.append("")
        path = self._storage_dir / f"book_{book_id}.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("结果已保存: %s", path)
        return str(path)

    # ── 资源清理 ──────────────────────────────────────────────────────

    async def close(self) -> None:
        """清理所有浏览器和 HTTP 会话。"""
        if self._playwright_context:
            try:
                await self._playwright_context.close()
            except Exception:
                pass
            self._playwright_context = None
        if self._playwright_browser:
            try:
                await self._playwright_browser.close()
            except Exception:
                pass
            self._playwright_browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        if self._curl_session:
            try:
                self._curl_session.close()
            except Exception:
                pass
            self._curl_session = None

    def close_sync(self) -> None:
        """同步方式清理资源。"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                logger.warning("事件循环正在运行，无法同步关闭；请使用 await engine.close()")
                return
            loop.run_until_complete(self.close())
        except RuntimeError:
            asyncio.run(self.close())

    # ══════════════════════════════════════════════════════════════════
    # 内部方法 — curl_cffi 隐身客户端
    # ══════════════════════════════════════════════════════════════════

    def _get_curl_session(self) -> CurlSession:
        """获取或创建 curl_cffi Session。"""
        if self._curl_session is None:
            if CurlSession is None:
                raise ImportError("curl_cffi 未安装，请执行: pip install curl-cffi")
            self._curl_session = CurlSession(impersonate="chrome131")
            if self.proxy:
                self._curl_session.proxies = {
                    "http": self.proxy,
                    "https": self.proxy,
                }
        return self._curl_session

    def _set_cookies_on_session(
        self, cookies: List[Dict[str, str]]
    ) -> None:
        """将 Cookie 列表注入到 curl_cffi session。"""
        session = self._get_curl_session()
        for c in cookies:
            name = c.get("name", "")
            value = c.get("value", "")
            if name and value:
                session.cookies.set(name, value)
        logger.info("已注入 %d 个 Cookie", len(cookies))

    def set_cookies_from_list(
        self, cookies: List[Dict[str, str]]
    ) -> None:
        """
        将 Playwright 导出的 Cookie 列表注入到 curl_cffi Session。

        通常在 bypass_waf_sync() 或 load_cookies() 之后调用。
        """
        self._set_cookies_on_session(cookies)

    def set_cookies_from_dict(self, cookies: Dict[str, str]) -> None:
        """从字典格式注入 Cookie。"""
        session = self._get_curl_session()
        for name, value in cookies.items():
            session.cookies.set(name, value)

    def _curl_get(
        self,
        session: CurlSession,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """带重试的 curl_cffi GET 请求。"""
        merged_headers = {**self.DEFAULT_HEADERS, **(headers or {})}
        last_error: Optional[Exception] = None

        for attempt in range(1, self.retry_times + 1):
            try:
                resp = session.get(
                    url, params=params, headers=merged_headers,
                    timeout=self.timeout, **kwargs,
                )
                status = getattr(resp, "status_code", 0)

                if status >= 500:
                    logger.warning(
                        "服务器错误 %d on %s (attempt %d/%d)",
                        status, url, attempt, self.retry_times,
                    )
                    if attempt < self.retry_times:
                        time.sleep(self.retry_delay * attempt)
                        continue

                if status == 429:
                    time.sleep(5.0)
                    if attempt < self.retry_times:
                        continue

                return self._normalize_response(resp)

            except Exception as e:
                last_error = e
                logger.warning(
                    "请求失败 on %s (attempt %d/%d): %s",
                    url, attempt, self.retry_times, e,
                )
                if attempt < self.retry_times:
                    time.sleep(self.retry_delay * attempt)

        return {
            "status_code": 0,
            "headers": {},
            "text": "",
            "json": None,
            "cookies": {},
            "error": str(last_error) if last_error else "max retries exceeded",
        }

    @staticmethod
    def _normalize_response(resp: Any) -> Dict[str, Any]:
        """将 curl_cffi 响应归一化为字典。"""
        try:
            body_json = resp.json()
        except (json.JSONDecodeError, ValueError, AttributeError):
            body_json = None
        cookies_dict: Dict[str, str] = {}
        try:
            for cookie in resp.cookies:
                cookies_dict[cookie.name] = cookie.value
        except Exception:
            pass
        return {
            "status_code": getattr(resp, "status_code", 0),
            "headers": dict(getattr(resp, "headers", {})),
            "text": getattr(resp, "text", ""),
            "json": body_json,
            "cookies": cookies_dict,
        }

    # ══════════════════════════════════════════════════════════════════
    # 内部方法 — Playwright WAF 绕过
    # ══════════════════════════════════════════════════════════════════

    async def _inject_stealth_js(self, page: Any) -> None:
        """注入反检测 JS 脚本。"""
        stealth_js = """
        const webdriverDescriptor = Object.getOwnPropertyDescriptor(
            Navigator.prototype, 'webdriver'
        );
        Object.defineProperty(Navigator.prototype, 'webdriver', {
            get: () => undefined,
            configurable: false,
        });
        if (navigator.permissions && navigator.permissions.query) {
            const originalQuery = navigator.permissions.query;
            navigator.permissions.query = (params) => {
                if (params.name === 'notifications') {
                    return Promise.resolve({ state: 'prompt', onchange: null });
                }
                if (params.name === 'clipboard-read') {
                    return Promise.resolve({ state: 'prompt', onchange: null });
                }
                return originalQuery(params);
            };
        }
        if (!navigator.connection) {
            Object.defineProperty(navigator, 'connection', {
                get: () => ({
                    effectiveType: '4g',
                    rtt: 50,
                    downlink: 10,
                    saveData: false,
                }),
                configurable: false,
            });
        }
        """
        await page.add_init_script(stealth_js)

    async def _wait_for_waf_pass(self, page: Any) -> None:
        """等待 WAF probe.js 挑战通过。"""
        waf_keywords = [
            "验证", "安全验证", "安全检查", "WAF",
            "腾讯云", "captcha", "verify", "challenge",
            "probe", "403", "access denied",
        ]
        success_indicators = ["qidian.com", ".qidian."]

        start_time = asyncio.get_event_loop().time()

        while True:
            elapsed_ms = (asyncio.get_event_loop().time() - start_time) * 1000
            if elapsed_ms > self.WAF_CHALLENGE_TIMEOUT_MS:
                logger.warning("WAF 挑战等待超时 (%dms)，继续执行", self.WAF_CHALLENGE_TIMEOUT_MS)
                return

            current_url = page.url
            is_waf_url = any(kw in current_url for kw in waf_keywords)
            is_normal_url = any(ind in current_url for ind in success_indicators)

            if not is_waf_url and is_normal_url:
                logger.info("WAF 挑战已通过 (URL 恢复正常)")
                return

            try:
                body_text = await page.evaluate(
                    "() => document.body ? document.body.innerText.substring(0, 2000) : ''"
                )
                body_lower = body_text.lower()
                has_waf_content = any(
                    kw in body_text or kw in body_lower for kw in waf_keywords
                )
                if not has_waf_content and len(body_text) > 100:
                    ready_state = await page.evaluate("document.readyState")
                    if ready_state == "complete":
                        logger.info("WAF 挑战已通过 (body 内容正常)")
                        return
            except Exception:
                pass

            await asyncio.sleep(self.WAF_POLL_INTERVAL_MS / 1000)

    # ══════════════════════════════════════════════════════════════════
    # 内部方法 — 正文提取 + 字体解码
    # ══════════════════════════════════════════════════════════════════

    # ── 正文可选选择器 ────────────────────────────────────────────────

    CONTENT_SELECTORS = [
        ".read-content.j_readContent",
        ".read-content",
        ".j_readContent",
        "#chapterContent",
        ".chapter-content",
        "#chaptercontent",
        ".content-wrap",
    ]

    AD_SELECTORS = [
        ".adsbygoogle",
        ".chapter-promotion",
        ".ad-wrap",
        ".recommend-box",
        ".report-chapter",
        ".chapter-footer",
        ".chapter-control",
        ".chapter-nav",
        ".j_chapterFooter",
        '[class*="ad-"]',
        '[id*="ad-"]',
        ".chapter-end",
        ".author-say",
    ]

    # ── 核心提取 ──────────────────────────────────────────────────────

    def _extract_full(self, html: str) -> Tuple[str, Dict]:
        """
        从 HTML 中提取正文并解码字体混淆。

        Returns:
            (纯文本正文, 元数据字典)
        """
        metadata = self._extract_metadata(html)

        if BeautifulSoup is None:
            logger.warning("BeautifulSoup 未安装，回退到正则提取")
            raw_text = self._extract_text_fallback(html)
            return self._clean_text(raw_text), metadata

        soup = BeautifulSoup(html, "html.parser")
        self._remove_ad_elements(soup)

        content_div = self._locate_content_div(soup)
        if content_div is None:
            logger.warning("未找到正文容器，回退到正则提取")
            raw_text = self._extract_text_fallback(html)
            return self._clean_text(raw_text), metadata

        paragraphs = self._extract_paragraphs(content_div)
        raw_text = "\n".join(paragraphs)

        if not raw_text.strip():
            return "", metadata

        if self._has_font_obfuscation(html, raw_text):
            logger.info("检测到字体混淆，开始解码")
            decoded = self._decode_font_via_fonttools(html)
            if decoded and decoded != html:
                decoded_soup = BeautifulSoup(decoded, "html.parser")
                decoded_div = self._locate_content_div(decoded_soup)
                if decoded_div:
                    decoded_paragraphs = self._extract_paragraphs(decoded_div)
                    raw_text = "\n".join(decoded_paragraphs)

        clean_text = self._clean_text(raw_text)
        return clean_text, metadata

    def _locate_content_div(self, soup: Any) -> Optional[Any]:
        """定位正文容器 div。"""
        if BsTag is None:
            return None
        for selector in self.CONTENT_SELECTORS:
            el = soup.select_one(selector)
            if el is not None:
                return el
        return None

    def _extract_paragraphs(self, content_div: Any) -> List[str]:
        """从正文容器提取段落文本。"""
        paragraphs = []
        p_tags = content_div.select("p")
        if p_tags:
            for p in p_tags:
                text = p.get_text(strip=True)
                if text:
                    paragraphs.append(text)
            return paragraphs

        for child in content_div.children:
            if isinstance(child, BsTag):
                text = child.get_text(strip=True)
                if text:
                    paragraphs.append(text)
            elif isinstance(child, str):
                text = child.strip()
                if text:
                    paragraphs.append(text)

        return paragraphs

    def _extract_text_fallback(self, html: str) -> str:
        """回退方案：使用正则提取正文。"""
        patterns = [
            r'<div[^>]*class="[^"]*read-content[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
            r'<div[^>]*class="[^"]*read-content[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*id="chapterContent"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*chapter-content[^"]*"[^>]*>(.*?)</div>',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                content = match.group(1)
                if BeautifulSoup:
                    soup = BeautifulSoup(content, "html.parser")
                    return soup.get_text(separator="\n", strip=True)

        if BeautifulSoup:
            soup = BeautifulSoup(html, "html.parser")
            return soup.get_text(separator="\n", strip=True)
        return ""

    def _remove_ad_elements(self, soup: Any) -> None:
        """移除广告和干扰元素。"""
        for selector in self.AD_SELECTORS:
            for element in soup.select(selector):
                element.decompose()
        for element in soup.find_all(
            lambda tag: tag.get("style")
            and (
                "display:none" in tag["style"]
                or "visibility:hidden" in tag["style"]
            )
        ):
            element.decompose()

    def _has_font_obfuscation(self, html: str, text: str) -> bool:
        """检测字体混淆特征。"""
        if re.search(r"@font-face", html, re.IGNORECASE):
            font_urls = self._extract_font_urls(html)
            if font_urls:
                return True
        if re.search(r"&#(?:10\d{3,4}|10\d{4,5}|[89]\d{4});", html):
            return True
        pua_chars = re.findall(r"[\ue000-\uf8ff\U000f0000-\U0010ffff]", text)
        if pua_chars:
            return True
        return False

    def _clean_text(self, text: str) -> str:
        """清洗文本。"""
        if not text:
            return ""
        text = self._decode_html_entities(text)
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if self._is_ad_line(line):
                continue
            cleaned.append(line)
        text = "\n".join(cleaned)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"^\s+", "", text, flags=re.MULTILINE)
        return text.strip()

    @staticmethod
    def _decode_html_entities(text: str) -> str:
        entities = {
            "&nbsp;": " ", "&lt;": "<", "&gt;": ">", "&amp;": "&",
            "&quot;": '"', "&apos;": "'", "&mdash;": "\u2014",
            "&ndash;": "\u2013", "&ldquo;": "\u201c", "&rdquo;": "\u201d",
            "&lsquo;": "\u2018", "&rsquo;": "\u2019", "&hellip;": "\u2026",
            "&middot;": "\u00b7",
        }
        for entity, char in entities.items():
            text = text.replace(entity, char)
        return text

    @staticmethod
    def _is_ad_line(line: str) -> bool:
        ad_patterns = [
            r"^广告.*[：:]",
            r"^.*(?:手机用户|浏览器.*阅读).*$",
            r"^(?:请记住|推荐|收藏|投推荐票|订阅).*$",
            r"^.*(?:www\.|\.com|\.cn)\s*$",
            r"^第[一二三四五六七八九十]+章.*$",
            r"^.*(?:一秒记住|首发|最新章节).*$",
            r"^.*(?:QQ|微信).*群.*\d+",
            r"^.{100,}$",
        ]
        for pattern in ad_patterns:
            if re.match(pattern, line):
                return True
        return False

    def _extract_metadata(self, html: str) -> Dict:
        """提取章节元数据。"""
        metadata: Dict = {}
        if BeautifulSoup is None:
            return metadata

        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.select_one("h3.j_chapterName")
        if title_tag:
            metadata["title"] = title_tag.get_text(strip=True)
        else:
            title_tag = soup.select_one(".chapter-title h3")
            if title_tag:
                metadata["title"] = title_tag.get_text(strip=True)

        if "title" not in metadata:
            title_tag = soup.select_one("title")
            if title_tag:
                title_text = title_tag.get_text(strip=True)
                parts = title_text.split(" - ")
                if parts:
                    metadata["title"] = parts[0]

        word_count = self._extract_word_count(soup, html)
        if word_count:
            metadata["word_count"] = word_count

        return metadata

    def _extract_word_count(self, soup: Any, html: str) -> Optional[int]:
        """提取字数。"""
        word_selectors = [
            ".j_chapterWord", ".chapter-word",
            ".word-count", '[class*="word"]',
        ]
        for selector in word_selectors:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                match = re.search(r"(\d+)", text)
                if match:
                    return int(match.group(1))

        patterns = [
            r'总字数[：:]?\s*(\d+)',
            r'字数[：:]?\s*(\d+)',
            r'word_count["\']?\s*[=:]\s*["\']?(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return int(match.group(1))
        return None

    # ══════════════════════════════════════════════════════════════════
    # 内部方法 — 字体反爬破解
    # ══════════════════════════════════════════════════════════════════

    # 数字字形特征指纹
    _DIGIT_FINGERPRINTS: Dict[str, Tuple[float, int, Tuple[int, int]]] = {
        "0": (0.75, 1, (20, 40)),
        "1": (0.25, 1, (10, 25)),
        "2": (0.70, 1, (25, 50)),
        "3": (0.65, 1, (25, 50)),
        "4": (0.70, 1, (20, 45)),
        "5": (0.65, 1, (25, 50)),
        "6": (0.70, 1, (25, 50)),
        "7": (0.65, 1, (15, 35)),
        "8": (0.70, 2, (30, 60)),
        "9": (0.70, 1, (25, 50)),
    }

    def _decode_font_via_fonttools(self, html: str) -> str:
        """使用 fontTools 解析 WOFF 字体文件并还原混淆文本。"""
        font_urls = self._extract_font_urls(html)
        for font_url in font_urls:
            mapping = self._fetch_font_mapping(font_url)
            if mapping and mapping.char_map:
                html = self._replace_obfuscated_chars(html, mapping)
        html = self._fallback_replace_pua(html)
        return html

    def _extract_font_urls(self, html: str) -> List[str]:
        """从 HTML 中提取自定义字体文件 URL。"""
        urls = []
        patterns = [
            r'@font-face\s*\{[^}]*src:\s*url\(["\']?([^"\'\)]+\.woff2?[^"\'\)]*)["\']?\)',
            r'src:\s*url\(["\']?([^"\'\)]+\.woff2?[^"\'\)]*)["\']?\)\s*format\(["\']woff',
            r'url\(["\']?(//?[\w./-]+\.woff2?)["\']?\)',
        ]
        seen: set = set()
        for pattern in patterns:
            for match in re.finditer(pattern, html, re.IGNORECASE | re.DOTALL):
                url = match.group(1).strip()
                if url.startswith("//"):
                    url = "https:" + url
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
        return urls

    def _fetch_font_mapping(self, font_url: str) -> Optional[FontMapping]:
        """下载 WOFF 字体并建立编码映射。"""
        logger.info("正在解析字体文件: %s", font_url)

        cached_path = self._cached_font_path(font_url)
        if cached_path.exists():
            font_data = cached_path.read_bytes()
            logger.debug("使用缓存字体: %s", cached_path)
        else:
            try:
                import requests
                resp = requests.get(
                    font_url,
                    headers={
                        "User-Agent": self.DEFAULT_HEADERS["User-Agent"],
                        "Referer": "https://read.qidian.com/",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                font_data = resp.content
                cached_path.write_bytes(font_data)
                logger.debug("字体已缓存: %s", cached_path)
            except Exception as e:
                logger.error("下载字体文件失败 %s: %s", font_url, e)
                return None

        try:
            char_map = self._parse_woff_mapping(font_data)
            logger.info(
                "字体映射已建立: %s → %d 个映射",
                font_url.split("/")[-1], len(char_map),
            )
            return FontMapping(
                font_family=f"qidian_font_{hashlib.md5(font_url.encode()).hexdigest()[:8]}",
                source_url=font_url,
                char_map=char_map,
            )
        except Exception as e:
            logger.error("fontTools 解析失败 %s: %s", font_url, e)
            return None

    def _parse_woff_mapping(self, font_data: bytes) -> Dict[str, str]:
        """解析 WOFF 字体文件，建立 PUA 编码 → 真实字符 映射。"""
        try:
            from fontTools.ttLib import TTFont
        except ImportError:
            logger.error("fontTools 未安装，请执行: pip install fonttools")
            return {}

        font = TTFont(BytesIO(font_data))
        cmap = font.getBestCmap()
        if not cmap:
            return {}

        pua_codes: Dict[int, str] = {}
        for codepoint, glyph_name in cmap.items():
            if self._is_pua(codepoint):
                pua_codes[codepoint] = glyph_name

        if not pua_codes:
            logger.debug("未找到 PUA 编码，跳过")
            font.close()
            return {}

        char_map: Dict[str, str] = {}
        glyph_set = font.getGlyphSet()
        glyph_features: List[Tuple[int, str, dict]] = []

        for codepoint, glyph_name in pua_codes.items():
            try:
                glyph = glyph_set[glyph_name]
                features = self._extract_glyph_features(glyph)
                if features:
                    glyph_features.append((codepoint, glyph_name, features))
            except Exception:
                pass

        if not glyph_features:
            font.close()
            return {}

        matched = self._match_glyphs_to_digits(glyph_features)
        for codepoint, real_char in matched.items():
            char_map[str(codepoint)] = real_char

        unmatched = [c for c, _, _ in glyph_features if c not in matched]
        if unmatched and len(matched) < 10:
            remaining = [d for d in "0123456789" if d not in matched.values()]
            for i, codepoint in enumerate(sorted(unmatched)):
                if i < len(remaining):
                    char_map[str(codepoint)] = remaining[i]

        font.close()
        return char_map

    @staticmethod
    def _is_pua(codepoint: int) -> bool:
        return (0xE000 <= codepoint <= 0xF8FF) or (
            0xF0000 <= codepoint <= 0x10FFFF
        )

    @staticmethod
    def _extract_glyph_features(glyph: Any) -> Optional[dict]:
        """提取 glyph 轮廓特征。"""
        try:
            coords = []
            if hasattr(glyph, "getCoordinates"):
                try:
                    glyph_coords, _ = glyph.getCoordinates()
                    coords = list(glyph_coords)
                except Exception:
                    pass

            num_contours = getattr(glyph, "numberOfContours", max(1, len(coords) // 10))
            x_min = getattr(glyph, "xMin", 0)
            y_min = getattr(glyph, "yMin", 0)
            x_max = getattr(glyph, "xMax", 0)
            y_max = getattr(glyph, "yMax", 0)
            glyph_width = x_max - x_min
            glyph_height = y_max - y_min

            if glyph_width <= 0 or glyph_height <= 0:
                return None

            return {
                "bounding_box": (x_min, y_min, x_max, y_max),
                "width": glyph_width,
                "height": glyph_height,
                "aspect_ratio": glyph_width / max(glyph_height, 1),
                "num_contours": num_contours,
                "num_points": len(coords),
            }
        except Exception:
            return None

    def _match_glyphs_to_digits(
        self, glyph_features: List[Tuple[int, str, dict]]
    ) -> Dict[int, str]:
        """通过字形特征匹配识别数字。"""
        matched: Dict[int, str] = {}
        sorted_by_ratio = sorted(
            glyph_features, key=lambda x: x[2]["aspect_ratio"]
        )

        if sorted_by_ratio:
            matched[sorted_by_ratio[0][0]] = "1"
        if len(sorted_by_ratio) > 1:
            matched[sorted_by_ratio[-1][0]] = "0"

        remaining = [(c, n, f) for c, n, f in glyph_features if c not in matched]
        unassigned = [d for d in "23456789"]

        for codepoint, _name, features in remaining:
            best_digit = None
            best_score = float("inf")
            for digit in unassigned:
                if digit in self._DIGIT_FINGERPRINTS:
                    fp = self._DIGIT_FINGERPRINTS[digit]
                    score = self._compute_match_score(features, fp)
                    if score < best_score:
                        best_score = score
                        best_digit = digit
            if best_digit and best_digit in unassigned:
                matched[codepoint] = best_digit
                unassigned.remove(best_digit)

        return matched

    @staticmethod
    def _compute_match_score(features: dict, fingerprint: Tuple) -> float:
        """计算字形匹配得分（越低越匹配）。"""
        expected_ratio, expected_contours, (min_pts, max_pts) = fingerprint
        score = 0.0
        aspect_diff = abs(features.get("aspect_ratio", 0.5) - expected_ratio)
        score += aspect_diff * 0.4
        contour_diff = abs(features.get("num_contours", 1) - expected_contours)
        score += contour_diff * 0.3
        num_pts = features.get("num_points", 0)
        if num_pts < min_pts:
            pts_diff = (min_pts - num_pts) / min_pts
        elif num_pts > max_pts:
            pts_diff = (num_pts - max_pts) / max_pts
        else:
            pts_diff = 0.0
        score += pts_diff * 0.3
        return score

    @staticmethod
    def _replace_obfuscated_chars(html: str, mapping: FontMapping) -> str:
        """替换 HTML 中的混淆字符。"""
        result = html
        for pua_code, real_char in mapping.char_map.items():
            result = result.replace(f"&#{pua_code};", real_char)
            result = result.replace(f"&#{pua_code}", real_char)
            try:
                hex_code = format(int(pua_code), "X")
                result = result.replace(f"&#x{hex_code};", real_char)
                result = result.replace(f"&#x{hex_code.lower()};", real_char)
            except (ValueError, TypeError):
                pass
        return result

    @staticmethod
    def _fallback_replace_pua(html: str) -> str:
        """回退替换残留的 PUA 编码。"""
        def _replace(match):
            try:
                code = int(match.group(1))
                if (0xE000 <= code <= 0xF8FF) or (0xF0000 <= code <= 0x10FFFF):
                    return " "
            except ValueError:
                pass
            return match.group(0)

        return re.sub(r"&#(\d+);", _replace, html)

    def _cached_font_path(self, font_url: str) -> Path:
        """获取字体文件的缓存路径。"""
        font_name = re.sub(r"[^a-zA-Z0-9]", "_", font_url.split("/")[-1])
        if len(font_name) > 64:
            font_name = hashlib.md5(font_url.encode()).hexdigest()
        return self._font_cache_dir / font_name
