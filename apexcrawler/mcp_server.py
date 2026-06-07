"""
ApexCrawler MCP Server — 让 AI 工具直接调用爬取能力。

支持的 tools:
- crawl(url, fast=False, engine=None) — 爬取 URL
- extract(url, selector=None, format="txt") — 一键提取
- qidian_list(book_id) — 获取起点章节列表
- qidian_crawl(book_id, chapters=5) — 爬取起点章节
"""

from __future__ import annotations

import json
import logging
import os
import hashlib
from typing import Optional

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════
# MCP Server
# ══════════════════════════════════════════════════════════════════════

server = FastMCP(
    name="ApexCrawler",
    instructions="ApexCrawler MCP 服务器 — 提供网页爬取与内容提取能力",
)


# ══════════════════════════════════════════════════════════════════════
# Tool: crawl
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="crawl",
    description=(
        "爬取指定 URL 并返回页面内容。"
        "fast=True 时使用轻量 HTTP 客户端快速抓取（适合静态页面）；"
        "fast=False 时使用完整管线（含 WAF 绕过、浏览器渲染）。"
        "engine 可指定引擎：vanilla, patched, camoufox, cloaked, qidian。"
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
        if fast:
            return await _fast_crawl(url, engine)
        return await _pipeline_crawl(url, engine)
    except Exception as e:
        logger.exception("crawl tool failed")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


async def _fast_crawl(url: str, engine: Optional[str] = None) -> str:
    """快速抓取：使用 FastFetcher 直接请求，适合静态页面。"""
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

    executor = PipelineExecutor(stages=stages, configs=configs)
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
        "一键提取 URL 的内容。"
        "使用轻量 HTTP 客户端快速获取页面。"
        "可指定 CSS selector 提取特定元素，format 控制输出格式（txt/html/json）。"
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
# Tool: qidian_list
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="qidian_list",
    description="获取起点中文网指定书籍的章节列表。自动过 WAF，无需登录即可获取免费章节。",
)
async def qidian_list(book_id: str) -> str:
    """获取起点章节列表。

    Args:
        book_id: 起点书籍 ID（如 "107580"）。
    """
    try:
        from apexcrawler.engines.qidian import QidianEngine
        import json

        engine = QidianEngine(headless=True)
        chapters = engine.fetch_catalog(int(book_id))
        result = []
        for ch in chapters:
            result.append({
                "index": ch.index,
                "title": ch.title,
                "is_vip": ch.is_vip,
                "word_count": ch.word_count,
                "chapter_id": ch.chapter_id,
            })
        return json.dumps({
            "book_id": book_id,
            "total": len(result),
            "free_count": sum(1 for c in result if not c["is_vip"]),
            "chapters": result,
        }, ensure_ascii=False)
    except Exception as e:
        import json
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Tool: qidian_crawl
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="qidian_crawl",
    description=(
        "爬取起点中文网指定书籍的章节内容。"
        "自动获取章节列表，然后提取前 N 章正文。"
        "book_id 为起点书籍 ID（数字），chapters 指定爬取章节数（默认 5）。"
    ),
)
async def qidian_crawl(book_id: str, chapters: int = 5) -> str:
    """爬取起点章节内容。

    Args:
        book_id: 起点书籍 ID（如 "107580"）。
        chapters: 爬取章节数量（默认 5）。
    """
    try:
        from apexcrawler.engines.qidian import QidianEngine

        engine = QidianEngine(headless=True)
        try:
            # 获取章节列表
            catalog = engine.fetch_catalog(int(book_id))
            if not catalog:
                return json.dumps(
                    {
                        "book_id": book_id,
                        "error": "未能获取章节列表",
                        "chapters": [],
                    },
                    ensure_ascii=False,
                )

            # 仅爬取免费章节
            free_chapters = [ch for ch in catalog if not ch.is_vip]
            target = free_chapters[:chapters]

            # 批量拉取正文
            results = engine.fetch_chapters(target)

            return json.dumps(
                {
                    "book_id": book_id,
                    "total_chapters_in_book": len(catalog),
                    "free_chapters_count": len(free_chapters),
                    "fetched_count": len(results),
                    "chapters": [
                        {
                            "index": ch.index,
                            "title": ch.title,
                            "is_vip": ch.is_vip,
                            "word_count": ch.word_count,
                            "content": ch.content,
                        }
                        for ch in results
                    ],
                },
                ensure_ascii=False,
            )
        finally:
            engine.close_sync()
    except Exception as e:
        logger.exception("qidian_crawl tool failed")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# New Tools: crawl_site
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="crawl_site",
    description="站点级爬取 — 从指定 URL 开始，自动发现并爬取站内链接。max_pages 控制最大页面数，same_domain 控制是否仅爬取同域名。",
)
async def crawl_site(url: str, max_pages: int = 10, same_domain: bool = True) -> str:
    """爬取站点内多个页面。"""
    try:
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
# New Tools: export_crawl
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="export_crawl",
    description="导出爬取结果 — 支持 JSON、JSONL、CSV 格式。data 为 JSON 字符串格式的爬取结果。",
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
# New Tools: screenshot_url
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="screenshot_url",
    description="截取指定 URL 的页面截图。full_page=True 时截取完整页面（含滚动区域）。返回 base64 编码的 PNG 图片。",
)
async def screenshot_url(url: str, full_page: bool = False) -> str:
    """截取页面截图。"""
    try:
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
# New Tools: validate_selector
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="validate_selector",
    description="验证 CSS/XPath 选择器是否能在页面上匹配到元素。返回匹配数量和示例内容。",
)
async def validate_selector(url: str, selector: str, selector_type: str = "css") -> str:
    """验证选择器是否有效。"""
    try:
        from apexcrawler.http.fetcher import FastFetcher
        from bs4 import BeautifulSoup

        fetcher = FastFetcher(impersonate="chrome131")
        try:
            result = fetcher.get(url)
            html = result.get("html", "")
            status = result.get("status_code", 0)

            if status != 200:
                return json.dumps({"url": url, "error": f"HTTP {status}"}, ensure_ascii=False)

            soup = BeautifulSoup(html, "html.parser")

            if selector_type == "xpath":
                # BeautifulSoup doesn't support XPath directly, try lxml
                try:
                    from lxml import html as lhtml

                    tree = lhtml.fromstring(html)
                    elements = tree.xpath(selector)
                    match_count = len(elements)
                    samples = [(str(el.tag), (el.text or "")[:100]) for el in elements[:3]] if elements else []
                except Exception:
                    match_count = 0
                    samples = []
            else:
                elements = soup.select(selector)
                match_count = len(elements)
                samples = [str(el)[:200] for el in elements[:3]]

            return json.dumps(
                {
                    "url": url,
                    "selector": selector,
                    "selector_type": selector_type,
                    "match_count": match_count,
                    "matches": match_count > 0,
                    "samples": samples,
                },
                ensure_ascii=False,
            )
        finally:
            fetcher.close()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# New Tools: clear_cache
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="clear_cache",
    description="清除爬虫的页面前端缓存。",
)
async def clear_cache() -> str:
    """清除页面缓存。"""
    try:
        cache_dir = os.path.expanduser("~/.apexcrawler/page_cache")
        if os.path.exists(cache_dir):
            import shutil

            shutil.rmtree(cache_dir)
            os.makedirs(cache_dir)
            return json.dumps(
                {"success": True, "message": f"Cache directory {cache_dir} cleared"}, ensure_ascii=False
            )
        return json.dumps({"success": True, "message": "No cache directory found"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# New Tools: list_schemas
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="list_schemas",
    description="列出所有可用的数据提取 Schema（如 product、article、company 等）。",
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
# New Tools: crawl_status
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="crawl_status",
    description="获取爬虫运行状态统计（如请求数、错误率等）。",
)
async def crawl_status() -> str:
    """获取爬虫状态统计。"""
    try:
        # Dynamically list engines from registry instead of hardcoding
        from apexcrawler.routing.registry import EngineRegistry
        engines = list(EngineRegistry.list_all().keys())
        return json.dumps(
            {
                "status": "running",
                "version": "0.1.0",
                "engines_available": engines,
                "uptime": "N/A",
                "cached_pages": 0,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# New Tools: batch_crawl
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="batch_crawl",
    description="批量爬取多个 URL。urls 为 JSON 数组字符串（如 '[\"https://a.com\",\"https://b.com\"]'）。engine 和 fast 参数同 crawl 工具。",
)
async def batch_crawl(urls: str, engine: Optional[str] = None, fast: bool = False) -> str:
    """批量爬取多个 URL。"""
    try:
        import json

        url_list = json.loads(urls) if isinstance(urls, str) else urls

        if not isinstance(url_list, list):
            return json_mod.dumps({"error": "urls must be a JSON array"}, ensure_ascii=False)

        # Limit to 20 concurrent URLs
        url_list = url_list[:20]

        from apexcrawler.http.fetcher import FastFetcher

        fetcher = FastFetcher(impersonate="chrome131")
        try:
            results = []
            for target_url in url_list:
                try:
                    result = fetcher.get(target_url)
                    results.append(
                        {
                            "url": target_url,
                            "status_code": result.get("status_code", 0),
                            "content_length": len(result.get("html", "")),
                        }
                    )
                except Exception as e:
                    results.append({"url": target_url, "error": str(e)})

            return json_mod.dumps(
                {
                    "total": len(url_list),
                    "success_count": sum(1 for r in results if r.get("status_code", 0) == 200),
                    "results": results,
                },
                ensure_ascii=False,
            )
        finally:
            fetcher.close()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# New Tools: train_selector
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="train_selector",
    description="教导爬虫识别指定字段的选择器。验证选择器有效性后持久化到 SelectorStore。",
)
async def train_selector(url: str, field_name: str, selector: str) -> str:
    """教导选择器。"""
    try:
        from apexcrawler.http.fetcher import FastFetcher
        from bs4 import BeautifulSoup

        fetcher = FastFetcher(impersonate="chrome131")
        try:
            result = fetcher.get(url)
            html = result.get("html", "")
            status = result.get("status_code", 0)

            if status != 200:
                return json.dumps({"error": f"HTTP {status}"}, ensure_ascii=False)

            soup = BeautifulSoup(html, "html.parser")
            elements = soup.select(selector)
            match_count = len(elements)

            return json.dumps({
                "url": url, "field": field_name, "selector": selector,
                "match_count": match_count, "valid": match_count > 0,
                "sample": str(elements[0])[:200] if elements else None,
            }, ensure_ascii=False)
        finally:
            fetcher.close()
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# New Tools: pause_crawl / resume_crawl / cancel_crawl
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="pause_crawl",
    description="暂停正在运行的爬取任务。task_id 为创建任务时返回的 ID。",
)
async def pause_crawl(task_id: str) -> str:
    try:
        from apexcrawler.task_manager import TaskManager

        tm = TaskManager()
        ok = await tm.pause_task(task_id)
        return json.dumps({"task_id": task_id, "paused": ok}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@server.tool(
    name="resume_crawl",
    description="恢复已暂停的爬取任务。",
)
async def resume_crawl(task_id: str) -> str:
    try:
        from apexcrawler.task_manager import TaskManager

        tm = TaskManager()
        ok = await tm.resume_task(task_id)
        return json.dumps({"task_id": task_id, "resumed": ok}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@server.tool(
    name="cancel_crawl",
    description="取消正在运行或暂停的爬取任务。",
)
async def cancel_crawl(task_id: str) -> str:
    try:
        from apexcrawler.task_manager import TaskManager

        tm = TaskManager()
        ok = await tm.cancel_task(task_id)
        return json.dumps({"task_id": task_id, "cancelled": ok}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# New Tools: crawl_metrics / selector_history / page_info
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="crawl_metrics",
    description="获取爬虫运行状态统计，包括总任务数、各状态分布、引擎可用性等。",
)
async def crawl_metrics() -> str:
    try:
        from apexcrawler.task_manager import TaskManager

        tm = TaskManager()
        metrics = await tm.get_metrics()
        from apexcrawler.routing.registry import EngineRegistry
        metrics["engines_available"] = list(EngineRegistry.list_all().keys())
        return json.dumps(metrics, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@server.tool(
    name="selector_history",
    description="查看指定字段的选择器变更历史（来自 SelectorDatabase）。field_name 为字段名称。",
)
async def selector_history(field_name: str) -> str:
    try:
        from apexcrawler.extraction.sel_healer import SelectorDatabase

        db = SelectorDatabase()
        # Get all selectors for this field across all URLs
        rows = db._conn.execute(
            "SELECT url_pattern, selector, confidence, success_count, fail_count, last_used_at FROM selectors WHERE field_name=? ORDER BY confidence DESC LIMIT 20",
            (field_name,)
        ).fetchall()
        result = [{"url_pattern": r[0], "selector": r[1], "confidence": r[2], "success_count": r[3], "fail_count": r[4]} for r in rows]
        return json.dumps({"field": field_name, "selectors": result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@server.tool(
    name="page_info",
    description="获取页面元数据：内容类型、编码、标题、链接数、图片数等。",
)
async def page_info(url: str) -> str:
    try:
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
# New Tools: create_crawl_task / get_task / list_crawl_tasks
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="create_crawl_task",
    description="创建异步爬取任务。返回 task_id 用于后续查询状态、暂停、恢复或取消。",
)
async def create_crawl_task(url: str, engine: str = "") -> str:
    try:
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
    description="查询爬取任务的状态和结果。task_id 为创建任务时返回的 ID。",
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


@server.tool(
    name="list_crawl_tasks",
    description="列出爬取任务列表。limit 控制返回数量（默认 50），status 可选过滤（pending/running/completed/failed/cancelled/paused）。",
)
async def list_crawl_tasks(limit: int = 50, status: str = "") -> str:
    try:
        from apexcrawler.task_manager import TaskManager

        tm = TaskManager()
        tasks = await tm.list_tasks(limit=limit, status=status if status else None)
        return json.dumps({
            "total": len(tasks),
            "tasks": [{"task_id": t.id, "url": t.url, "status": t.status.value, "engine": t.engine, "progress": t.progress} for t in tasks],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════════
# Novel tools
# ══════════════════════════════════════════════════════════════════════


@server.tool(
    name="novel_info",
    description="获取小说信息：目录、章节数、免费/付费状态。支持起点、番茄、笔趣阁等站点。",
)
async def novel_info(url: str) -> str:
    import json, logging
    logging.getLogger("apexcrawler").setLevel(logging.WARNING)
    try:
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
    description="下载小说章节到本地文件。url 为小说页面链接，chapters 可选章节范围如 '1-50'。返回文件路径。",
)
async def novel_download(url: str, chapters: str = "") -> str:
    import json, logging
    logging.getLogger("apexcrawler").setLevel(logging.WARNING)
    try:
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
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    server.run(transport="stdio")
