"""
ApexCrawler MCP Server — 让 AI 工具直接调用爬取能力。

支持的 tools:
- crawl(url, fast=False, engine=None) — 爬取 URL
- extract(url, selector=None, format="txt") — 一键提取
- search(query, num=10) — 搜索网络
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import hashlib
import socket
from typing import Optional
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# SSRF Protection
# ══════════════════════════════════════════════════════════════════════

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]


def _validate_url(url: str) -> None:
    """Validate URL to prevent SSRF attacks. Raises ValueError if blocked."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Blocked scheme: {parsed.scheme}")
    host = parsed.hostname or ""
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        raise ValueError(f"Blocked host: {host}")
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # Not a literal IP — resolve via DNS
        try:
            resolved = socket.gethostbyname(host)
            addr = ipaddress.ip_address(resolved)
        except socket.gaierror:
            return  # Cannot resolve — let the caller handle
    else:
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise ValueError(f"Blocked IP: {host} (in {net})")

# ══════════════════════════════════════════════════════════════════════
# MCP Server
# ══════════════════════════════════════════════════════════════════════

server = FastMCP(
    name="ApexCrawler",
    instructions="ApexCrawler MCP Server — provides web crawling, content extraction, search, and URL inspection capabilities",
)


# ══════════════════════════════════════════════════════════════════════
# Tool: crawl
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="crawl",
    description=(
        "Crawl a URL and return page content. "
        "Use fast=True for quick HTTP fetch (static pages) or fast=False for full pipeline "
        "(WAF bypass, browser rendering). "
        "Specify engine: vanilla, patched, camoufox, cloaked, qidian."
    ),
)
async def crawl(
    url: str,
    fast: bool = False,
    engine: Optional[str] = None,
) -> str:
    """爬取指定 URL。

    Args:
        url: 目标 URL。
        fast: 使用快速模式（轻量 HTTP 客户端）。
        engine: 指定引擎（vanilla/patched/camoufox/cloaked/qidian），
                不指定则由管线自动选择。
    """
    try:
        _validate_url(url)
        if fast:
            return await _fast_crawl(url, engine)
        return await _pipeline_crawl(url, engine)
    except Exception as e:
        logger.exception("crawl tool failed")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def _fast_crawl(url: str, engine: Optional[str] = None) -> str:
    """快速抓取：使用 FastFetcher 直接请求，适合静态页面。"""
    _validate_url(url)  # 首次检查

    # DNS 重绑定防护：在请求前解析 IP 并再次验证
    parsed = urlparse(url)
    host = parsed.hostname or ""
    try:
        addrs = socket.getaddrinfo(host, 80)
        resolved_ips = set()
        for addr in addrs:
            ip = addr[4][0]
            if ip not in resolved_ips:
                resolved_ips.add(ip)
                if ipaddress.ip_address(ip).version == 6:
                    _validate_url(f"http://[{ip}]/")
                else:
                    _validate_url(f"http://{ip}/")
    except Exception:
        pass  # 如果 DNS 解析失败，依赖首次验证

    from apexcrawler.http.fetcher import FastFetcher

    fetcher = FastFetcher(impersonate="chrome131")
    try:
        result = fetcher.get(url)
        status = result.get("status_code", 0)
        text = result.get("text", "")

        return json.dumps(
            {
                "url": url,
                "status_code": status,
                "content_length": len(text),
                "content": text[:50000],
            },
            ensure_ascii=False,
        )
    finally:
        fetcher.close()


async def _pipeline_crawl(url: str, engine: Optional[str] = None) -> str:
    """管线爬取：使用 ApexCrawler 完整管线，含 WAF 绕过与浏览器渲染。"""
    from apexcrawler.core.context import PipelineContext
    from apexcrawler.pipeline.core import PipelineExecutor, StageConfig, RetryPolicy
    from apexcrawler.pipeline.stages import (
        ScheduleStage,
        RouteStage,
        EvadeStage,
        ExtractStage,
        ValidateStage,
        StoreStage,
    )

    # 构建默认管线阶段
    stages = [
        ScheduleStage(),
        RouteStage(),
        EvadeStage(),
        ExtractStage(),
        ValidateStage(),
        StoreStage(),
    ]

    # 配置各阶段超时
    configs = {
        "schedule": StageConfig(timeout=5.0),
        "route": StageConfig(timeout=10.0),
        "evade": StageConfig(timeout=10.0),
        "extract": StageConfig(
            timeout=60.0,
            retry=RetryPolicy(max_retries=2, base_delay=2.0),
        ),
        "validate": StageConfig(timeout=10.0),
        "store": StageConfig(timeout=10.0),
    }

    executor = PipelineExecutor(
        stages=stages,
        configs=configs,
        settings=None,
        session_manager=None,
        rate_controller=None,
        degrade_manager=None,
        plugin_manager=None,
    )
    ctx = PipelineContext(target_url=url)

    if engine:
        ctx.selected_engine = engine

    success, result_ctx = await executor.run(ctx)

    if not success:
        error = result_ctx.fatal_error or result_ctx.stage_errors
        return json.dumps(
            {"url": url, "success": False, "error": str(error)},
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "url": url,
            "success": True,
            "status_code": result_ctx._last_status,
            "content_length": len(result_ctx.raw_html or ""),
            "engine_used": result_ctx.selected_engine,
            "confidence": result_ctx.extraction_confidence,
            "content": (result_ctx.raw_html or "")[:50000],
        },
        ensure_ascii=False,
    )


# ══════════════════════════════════════════════════════════════════════
# Tool: extract
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="extract",
    description=(
        "Extract content from a URL. "
        "Uses lightweight HTTP client. "
        "Optional CSS selector to extract specific elements. "
        "Format: txt (plain text), html (raw HTML), json (structured JSON)."
    ),
)
async def extract(
    url: str,
    selector: Optional[str] = None,
    format: str = "txt",
) -> str:
    """提取 URL 内容。

    Args:
        url: 目标 URL。
        selector: CSS 选择器，提取特定元素（可选）。
        format: 输出格式，可选 "txt"（纯文本）、"html"（原始 HTML）、"json"（结构化 JSON）。
    """
    try:
        _validate_url(url)
        from apexcrawler.http.fetcher import FastFetcher

        fetcher = FastFetcher(impersonate="chrome131")
        try:
            result = fetcher.get(url)
            status = result.get("status_code", 0)
            html = result.get("html", "")

            if status != 200:
                return json.dumps(
                    {
                        "url": url,
                        "status_code": status,
                        "error": f"HTTP {status}",
                    },
                    ensure_ascii=False,
                )

            if selector:
                extracted = _extract_with_selector(html, selector)
            else:
                extracted = html

            if format == "txt":
                content = _html_to_text(extracted)
                return content[:100000]
            elif format == "json":
                return json.dumps(
                    {
                        "url": url,
                        "status_code": status,
                        "content_length": len(html),
                        "content": html[:50000],
                    },
                    ensure_ascii=False,
                )
            else:
                return extracted[:100000]
        finally:
            fetcher.close()
    except Exception as e:
        logger.exception("extract tool failed")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def _extract_with_selector(html: str, selector: str) -> str:
    """使用 CSS 选择器提取元素。"""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        elements = soup.select(selector)
        if not elements:
            return f"<!-- 未找到匹配选择器 '{selector}' 的元素 -->"
        return "\n\n".join(str(el) for el in elements)
    except ImportError:
        return "<!-- BeautifulSoup 未安装，无法使用 CSS 选择器 -->"
    except Exception as e:
        return f"<!-- 选择器提取错误: {e} -->"


def _html_to_text(html: str) -> str:
    """将 HTML 转换为纯文本。"""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        return "\n".join(lines)
    except ImportError:
        import re

        clean = re.sub(r"<[^>]+>", " ", html)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean


# ══════════════════════════════════════════════════════════════════════
# Tool: crawl_site
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="crawl_site",
    description="Site-level crawl — start from a URL, auto-discover and crawl internal links. max_pages controls page limit, same_domain restricts to same domain.",
)
async def crawl_site(url: str, max_pages: int = 10, same_domain: bool = True) -> str:
    """爬取站点内多个页面。"""
    try:
        _validate_url(url)
        from apexcrawler.http.fetcher import FastFetcher
        from urllib.parse import urlparse, urljoin
        from bs4 import BeautifulSoup

        fetcher = FastFetcher(impersonate="chrome131")
        try:
            base_domain = urlparse(url).netloc
            visited = set()
            to_visit = [url]
            results = []

            while to_visit and len(visited) < max_pages:
                current_url = to_visit.pop(0)
                if current_url in visited:
                    continue
                visited.add(current_url)

                result = fetcher.get(current_url)
                html = result.get("html", "")
                status = result.get("status_code", 0)

                results.append(
                    {
                        "url": current_url,
                        "status_code": status,
                        "content_length": len(html),
                    }
                )

                # Extract links for further crawling
                if status == 200 and html:
                    soup = BeautifulSoup(html, "html.parser")
                    for a in soup.find_all("a", href=True):
                        link = urljoin(current_url, a["href"])
                        link_parsed = urlparse(link)
                        if link_parsed.scheme in ("http", "https") and link not in visited:
                            if not same_domain or link_parsed.netloc == base_domain:
                                to_visit.append(link)

            return json.dumps(
                {
                    "start_url": url,
                    "pages_crawled": len(results),
                    "results": results,
                },
                ensure_ascii=False,
            )
        finally:
            fetcher.close()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Tool: export_crawl
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="export_crawl",
    description="Export crawl results in JSON, JSONL, or CSV format. Pass data as a JSON string of crawl results.",
)
async def export_crawl(data: str, format: str = "json") -> str:
    """导出爬取结果为指定格式。"""
    try:
        import csv
        import io

        parsed = json.loads(data) if isinstance(data, str) else data

        if format == "jsonl":
            lines = [json.dumps(item, ensure_ascii=False) for item in parsed]
            return "\n".join(lines)
        elif format == "csv":
            if isinstance(parsed, list) and parsed:
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=list(parsed[0].keys()))
                writer.writeheader()
                writer.writerows(parsed)
                return output.getvalue()
            else:
                return json.dumps({"error": "CSV export requires a non-empty array of objects"}, ensure_ascii=False)
        else:
            return json.dumps(parsed, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Tool: screenshot_url
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="screenshot_url",
    description="Take a screenshot of a URL. full_page=True captures full page (including scroll area). Returns base64-encoded PNG.",
)
async def screenshot_url(url: str, full_page: bool = False) -> str:
    """截取页面截图。"""
    try:
        _validate_url(url)
        from playwright.async_api import async_playwright
        import base64

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=30000)
                screenshot_bytes = await page.screenshot(full_page=full_page)
                b64 = base64.b64encode(screenshot_bytes).decode("ascii")
                return json.dumps(
                    {
                        "url": url,
                        "full_page": full_page,
                        "screenshot_base64": b64,
                        "size_bytes": len(screenshot_bytes),
                    },
                    ensure_ascii=False,
                )
            finally:
                await browser.close()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Tool: list_schemas
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="list_schemas",
    description="List all available data extraction schemas (e.g. product, article, company).",
)
async def list_schemas() -> str:
    """列出可用 Schema。"""
    try:
        from apexcrawler.extraction.schema import list_schemas

        schemas = list_schemas()
        return json.dumps(
            {
                "schemas": schemas,
                "count": len(schemas),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Tool: page_info
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="page_info",
    description="Get page metadata: content type, encoding, title, link count, image count, etc.",
)
async def page_info(url: str) -> str:
    try:
        _validate_url(url)
        from apexcrawler.http.fetcher import FastFetcher
        from bs4 import BeautifulSoup
        from urllib.parse import urlparse

        fetcher = FastFetcher(impersonate="chrome131")
        try:
            result = fetcher.get(url)
            html = result.get("html", "")
            status = result.get("status_code", 0)
            headers = result.get("headers", {})

            info = {
                "url": url, "domain": urlparse(url).netloc,
                "status_code": status, "content_length": len(html),
                "content_type": headers.get("Content-Type", headers.get("content-type", "")),
            }

            if status == 200 and html:
                soup = BeautifulSoup(html, "html.parser")
                info["title"] = (soup.title.string if soup.title else "")[:200]
                info["link_count"] = len(soup.find_all("a", href=True))
                info["image_count"] = len(soup.find_all("img"))
                info["text_length"] = len(soup.get_text(strip=True))

            return json.dumps(info, ensure_ascii=False)
        finally:
            fetcher.close()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Tools: create_crawl_task / get_task
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="create_crawl_task",
    description="Create an async crawl task. Returns task_id for status queries, pause, resume, or cancel.",
)
async def create_crawl_task(url: str, engine: str = "") -> str:
    try:
        _validate_url(url)
        from apexcrawler.task_manager import TaskManager

        tm = TaskManager()
        task = await tm.create_task(url=url, engine=engine)
        return json.dumps({
            "task_id": task.id, "url": task.url, "status": task.status.value,
            "engine": task.engine,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@server.tool(
    name="get_task",
    description="Query crawl task status and result by task_id.",
)
async def get_task(task_id: str) -> str:
    try:
        from apexcrawler.task_manager import TaskManager

        tm = TaskManager()
        task = await tm.get_task(task_id)
        if task is None:
            return json.dumps({"error": f"Task {task_id} not found"}, ensure_ascii=False)
        return json.dumps({
            "task_id": task.id, "url": task.url, "status": task.status.value,
            "engine": task.engine, "progress": task.progress,
            "error": task.error, "result": task.result,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Novel tools
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="novel_info",
    description="Get novel information: table of contents, chapter count, free/paid status. Supports Qidian, Fanqie, Biquge, etc.",
)
async def novel_info(url: str) -> str:
    import json, logging
    logging.getLogger("apexcrawler").setLevel(logging.WARNING)
    try:
        _validate_url(url)
        from apexcrawler.novel.engine import NovelEngine
        ne = NovelEngine()
        book = ne.info(url)
        result = {
            "book_id": book.book_id,
            "total": len(book.chapters),
            "free_count": sum(1 for c in book.chapters if not c.is_vip),
            "chapters": [
                {"index": c.index, "title": c.title, "is_vip": c.is_vip}
                for c in book.chapters[:50]
            ],
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@server.tool(
    name="novel_download",
    description="Download novel chapters to a local file. url is the novel page link, chapters specifies range (e.g. '1-50'). Returns file path.",
)
async def novel_download(url: str, chapters: str = "") -> str:
    import json, logging
    logging.getLogger("apexcrawler").setLevel(logging.WARNING)
    try:
        _validate_url(url)
        from apexcrawler.novel.engine import NovelEngine
        start, end = 1, 0
        if chapters:
            parts = chapters.split("-")
            start = int(parts[0])
            if len(parts) > 1:
                end = int(parts[1])
        ne = NovelEngine()
        path = ne.download(url, start=start, end=end)
        return json.dumps({"file": path, "chapters_range": f"{start}-{end or 'all'}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Tool: search
# ══════════════════════════════════════════════════════════════════════


@server.tool(name="search", description="Search the web and return structured results (title, link, snippet).")
async def search_web_tool(query: str, num: int = 10) -> str:
    """Search the web using configured search provider.
    
    Args:
        query: Search query (e.g. "Python web scraping tutorial")
        num: Number of results to return (default 10, max 20)
    
    Returns:
        JSON string with search results
    """
    from apexcrawler.search import search_web
    results = await search_web(query=query, num=min(num, 20))
    return json.dumps([r.to_dict() for r in results], ensure_ascii=False)


@server.tool(
    name="inspect_url",
    description="Comprehensive URL inspection (browser capture + infrastructure OSINT) — categorizes third-party resources, detects CDN/analytics/ads, analyzes DNS/WHOIS/IP.",
)
async def inspect_url_tool(url: str, headless: bool = True, timeout: int = 30) -> str:
    """Perform a comprehensive security inspection of a target URL.

    Uses CloakBrowser to render the page and intercept all network requests,
    then categorizes third-party resources, detects CDNs, analytics, ads, and trackers.
    Also performs infrastructure OSINT analysis (DNS, WHOIS, IP, SSL, CDN detection).

    Args:
        url: Target URL to inspect (e.g. "https://example.com")
        headless: Whether to run the browser in headless mode (default True)
        timeout: Browser navigation timeout in seconds (default 30)

    Returns:
        JSON string with the full inspection report
    """
    from apexcrawler.inspector import inspect_url

    _validate_url(url)
    report = await inspect_url(url, headless=headless, timeout=timeout)

    def _to_dict(obj):
        if hasattr(obj, '__dataclass_fields__'):
            return {f: _to_dict(getattr(obj, f)) for f in obj.__dataclass_fields__}
        if isinstance(obj, list):
            return [_to_dict(i) for i in obj]
        if isinstance(obj, dict):
            return {k: _to_dict(v) for k, v in obj.items()}
        return str(obj) if obj is not None else None

    return json.dumps(_to_dict(report), ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    server.run(transport="stdio")
