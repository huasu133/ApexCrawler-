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
    description="获取起点中文网指定书籍的章节列表。book_id 为起点书籍 ID（数字）。",
)
async def qidian_list(book_id: str) -> str:
    """获取起点章节列表。

    Args:
        book_id: 起点书籍 ID（如 "107580"）。
    """
    try:
        from apexcrawler.api.qidian import CatalogFetcher

        fetcher = CatalogFetcher()
        data = fetcher.get_free_chapters(book_id)

        return json.dumps(
            {
                "book_id": book_id,
                "total": data.get("total", 0),
                "free_count": data.get("free_count", 0),
                "vip_count": data.get("vip_count", 0),
                "chapters": [
                    {
                        "title": ch.get("title", ""),
                        "index": ch.get("index", 0),
                        "is_free": ch.get("is_free", False),
                        "words_count": ch.get("words_count", 0),
                    }
                    for ch in data.get("chapters", [])
                ],
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("qidian_list tool failed")
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
# CLI entry point
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    server.run(transport="stdio")
