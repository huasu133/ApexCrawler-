"""
ApexCrawler Interactive Shell — 实时测试爬取命令。

用法:
    apex shell

在 Shell 中可用的命令:
    crawl <url>         — 测试爬取
    extract <url>       — 测试提取
    parse <url>         — 解析页面结构
    engines             — 列出可用引擎
    config              — 查看当前配置
    history             — 显示历史命令
    help                — 帮助
    exit                — 退出
"""

from __future__ import annotations

import asyncio
import readline
import shlex
import sys
from pathlib import Path
from typing import NoReturn

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.syntax import Syntax
from rich import box

from ..config.schema import Settings
from ..http.fetcher import FastFetcher

console = Console()

# ── 引擎信息 ────────────────────────────────────────────────

_ENGINE_INFO = [
    ("vanilla",    "轻量级 Playwright Chromium，适合简单页面",              "playwright, chromium, lightweight"),
    ("cloaked",    "高匿引擎（CloakBrowser），最高指纹保护",                  "cloakbrowser, chromium, stealth, wasm"),
    ("camoufox",   "伪装 Firefox 引擎，JA4 指纹多样性最高",                   "camoufox, firefox, ja4-diverse, stealth"),
    ("patched",    "补丁版 Chromium（Patchright），DOM 自动化能力强",          "patchright, chromium, dom-patch"),
]

_HELP_TEXT = """
[bold cyan]ApexCrawler Interactive Shell[/] — 实时测试爬取命令

[bold]可用命令:[/]

  [green]crawl <url>[/]          测试爬取，展示引擎选择、耗时、内容摘要
  [green]extract <url>[/]        测试内容提取，展示提取结果
  [green]parse <url>[/]         解析页面结构（标题、meta、链接数、正文长度等）
  [green]engines[/]             列出所有可用引擎及其说明
  [green]config[/]              显示当前配置
  [green]history[/]             显示命令历史
  [green]help[/]                显示本帮助信息
  [green]exit[/] 或 [green]quit[/]    退出 Shell

[dim]提示: 按 ↑/↓ 浏览历史，Ctrl+D 退出[/]
"""


# ── 工具函数 ────────────────────────────────────────────────

def _parse_crawl_result(result: dict) -> str:
    """Parse a crawl result dict into a summary string."""
    parts = []
    status = result.get("status", "unknown")
    parts.append(f"[{'green' if status == 'success' else 'red'}]{status.upper()}[/]")
    if trace_id := result.get("trace_id"):
        parts.append(f"trace={trace_id}")
    if engine := result.get("engine"):
        parts.append(f"engine={engine}")
    if html_bytes := result.get("html_bytes"):
        parts.append(f"html={html_bytes}B")
    if duration := result.get("duration_s"):
        parts.append(f"duration={duration}s")
    if valid := result.get("valid"):
        parts.append(f"valid={valid}")
    if errors := result.get("errors"):
        parts.append(f"errors={errors}")
    if error := result.get("error"):
        parts.append(f"error={error}")
    return "  ".join(parts)


async def _async_crawl(url: str) -> dict:
    """Asynchronously crawl a URL and return result summary."""
    from ..core.context import PipelineContext
    from ..extraction.schema import get_schema
    from ..pipeline.stages import (
        ScheduleStage, RouteStage, EvadeStage,
        ExtractStage, ValidateStage, FontDecodeStage, StoreStage,
    )
    from ..pipeline.core import PipelineExecutor, StageConfig, RetryPolicy
    from ..behavior.timing import TimingScheduler
    from ..pipeline.session_manager import SessionManager
    from ..pipeline.rate_controller import RateController
    from ..http.connection_pool import ConnectionReuseManager
    from ..http.tls_router import TLSRouter
    from ..pipeline.degrade import DegradeManager
    from ..engines.pool import EnginePool
    from ..engines import vanilla, cloaked, camouflaged, patched

    import os
    for _k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        os.environ.pop(_k, None)

    try:
        settings = Settings.from_yaml()
    except Exception:
        settings = Settings()

    schema = get_schema("generic")
    ctx_obj = PipelineContext(
        target_url=url,
        extraction_schema=schema,
        selected_engine="vanilla",
    )

    session_mgr = SessionManager()
    rate_ctrl = RateController()
    conn_mgr = ConnectionReuseManager()
    tls_router = TLSRouter()
    engine_pool = EnginePool()
    timing = TimingScheduler()

    stages = [
        ScheduleStage(timing=timing),
        RouteStage(),
        EvadeStage(router=tls_router),
        ExtractStage(engine_factory=engine_pool, conn_manager=None),
        FontDecodeStage(),
        ValidateStage(),
        StoreStage(),
    ]
    configs = {
        "extract": StageConfig(timeout=30, retry=RetryPolicy(max_retries=2)),
        "schedule": StageConfig(timeout=10, retry=RetryPolicy(max_retries=0)),
    }
    degrade_mgr = DegradeManager()
    from ..plugins import PluginManager
    from ..plugins.builtin import LoggingPlugin
    plugin_mgr = PluginManager()
    plugin_mgr.register(LoggingPlugin())

    executor = PipelineExecutor(
        stages, configs,
        settings=settings,
        session_manager=session_mgr,
        rate_controller=rate_ctrl,
        degrade_manager=degrade_mgr,
        plugin_manager=plugin_mgr,
    )

    ok, result = await executor.run(ctx_obj)
    await engine_pool.close_all()

    return {
        "url": url,
        "trace_id": result.trace_id,
        "status": "success" if ok else "failed",
        "engine": result.selected_engine,
        "html_bytes": len(result.raw_html or ""),
        "duration_s": round(result.duration(), 2),
        "valid": result.validation_passed,
        "stored_id": result.stored_id,
        "errors": result.validation_errors,
    }


async def _async_extract(url: str, selector: str | None = None) -> dict:
    """Asynchronously extract content from a URL.

    Returns a dict with 'status', 'content', 'selector', and optionally 'error'.
    """
    from ..http.fetcher import FastFetcher

    fetcher = FastFetcher(timeout=30)
    try:
        result = fetcher.get(url)
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        fetcher.close()

    if result["status_code"] != 200:
        return {
            "status": "error",
            "error": f"HTTP {result['status_code']}",
            "html_len": len(result.get("html", "")),
        }

    html: str = result["html"]
    status_code = result["status_code"]

    if not html.strip():
        return {"status": "error", "error": "Empty response body"}

    try:
        from lxml.html import fromstring
    except ImportError:
        return {"status": "error", "error": "lxml is required for extraction"}

    doc = fromstring(html)
    extracted: list[dict[str, str]] = []

    if selector:
        elements = doc.cssselect(selector)
        for el in elements:
            extracted.append({
                "tag": el.tag if hasattr(el, "tag") else "",
                "text": el.text_content().strip(),
            })
    else:
        content_el = doc.cssselect("article")
        if not content_el:
            content_el = doc.cssselect("main")
        if not content_el:
            content_el = doc.cssselect("body")
        if content_el:
            raw_text = content_el[0].text_content().strip()
        else:
            raw_text = doc.text_content().strip()

        import re
        lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
        clean = " ".join(lines)
        clean = re.sub(r"\s{2,}", " ", clean)
        extracted = [{"tag": "content", "text": clean}]

    return {
        "status": "ok",
        "status_code": status_code,
        "html_len": len(html),
        "selector": selector or "auto",
        "count": len(extracted),
        "results": extracted[:10],  # limit to first 10
    }


async def _async_parse(url: str) -> dict:
    """Asynchronously parse a URL and return page structure info."""
    from ..http.fetcher import FastFetcher
    import re

    fetcher = FastFetcher(timeout=30)
    try:
        result = fetcher.get(url)
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        fetcher.close()

    if result["status_code"] != 200:
        return {"status": "error", "error": f"HTTP {result['status_code']}"}

    html: str = result["html"]
    status_code = result["status_code"]

    # Extract useful info using simple regex / lxml
    title = ""
    description = ""
    keywords = ""
    link_count = 0
    text_length = 0

    m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
    if m:
        title = m.group(1).strip()

    m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html, re.IGNORECASE)
    if m:
        description = m.group(1)

    m = re.search(r'<meta\s+name="keywords"\s+content="([^"]+)"', html, re.IGNORECASE)
    if m:
        keywords = m.group(1)

    link_count = len(re.findall(r'<a\s+', html, re.IGNORECASE))

    # Extract body text length
    m = re.search(r"<body[^>]*>([\s\S]*)</body>", html, re.IGNORECASE)
    if m:
        body_text = re.sub(r"<[^>]+>", "", m.group(1))
        body_text = re.sub(r"\s+", " ", body_text).strip()
        text_length = len(body_text)

    # Find h1
    h1 = ""
    m = re.search(r"<h1[^>]*>([^<]+)</h1>", html, re.IGNORECASE)
    if m:
        h1 = m.group(1).strip()

    return {
        "status": "ok",
        "url": url,
        "status_code": status_code,
        "html_size": len(html),
        "title": title,
        "h1": h1,
        "description": description,
        "keywords": keywords,
        "link_count": link_count,
        "text_length": text_length,
    }


# ── Shell 命令实现 ──────────────────────────────────────────

def _cmd_crawl(args: list[str]) -> None:
    """crawl <url> — 测试爬取，展示引擎选择、耗时、内容摘要。"""
    if not args:
        console.print("[red]用法: crawl <url>[/]")
        return
    url = args[0]

    console.print(f"\n[bold]爬取:[/] {url}")
    with console.status("[yellow]正在爬取...[/]", spinner="dots") as _status:
        result = asyncio.run(_async_crawl(url))

    console.print()
    console.print(Panel(
        _parse_crawl_result(result),
        title=f"[bold]爬取结果[/]",
        border_style="cyan",
    ))

    # Show content preview
    if result.get("status") == "success":
        html_bytes = result.get("html_bytes", 0)
        duration = result.get("duration_s", 0)
        engine = result.get("engine", "?")
        console.print(
            f"  [green]✓[/] HTML: {html_bytes:,} B  |  "
            f"耗时: {duration:.1f}s  |  "
            f"引擎: {engine}"
        )


def _cmd_extract(args: list[str]) -> None:
    """extract <url> [-s selector] — 测试内容提取。"""
    if not args:
        console.print("[red]用法: extract <url> [-s selector][/]")
        return

    selector = None
    url = args[0]

    # Check for -s/--selector flag
    if len(args) >= 3 and args[1] in ("-s", "--selector"):
        selector = args[2]
    elif len(args) >= 2 and args[1].startswith("-"):
        console.print(f"[red]未知选项: {args[1]}[/]")
        return

    console.print(f"\n[bold]提取:[/] {url}")
    if selector:
        console.print(f"  选择器: [cyan]{selector}[/]")

    with console.status("[yellow]正在提取...[/]", spinner="dots") as _status:
        result = asyncio.run(_async_extract(url, selector))

    if result.get("status") == "error":
        console.print(f"[red]提取失败: {result.get('error')}[/]")
        return

    console.print()
    console.print(Panel(
        f"状态: HTTP {result.get('status_code')}  |  "
        f"HTML: {result.get('html_len', 0):,} B  |  "
        f"匹配: {result.get('count', 0)} 个元素",
        title=f"[bold]提取摘要[/]",
        border_style="green",
    ))

    results = result.get("results", [])
    if not results:
        console.print("[yellow]未提取到内容[/]")
        return

    console.print()
    for i, item in enumerate(results, 1):
        tag = item.get("tag", "")
        text = item.get("text", "")
        if len(text) > 500:
            text = text[:500] + "..."
        console.print(f"  [cyan]#{i}[/] [dim]&lt;{tag}&gt;[/]")
        console.print(f"    {text}")
        console.print()


def _cmd_parse(args: list[str]) -> None:
    """parse <url> — 解析页面结构。"""
    if not args:
        console.print("[red]用法: parse <url>[/]")
        return
    url = args[0]

    console.print(f"\n[bold]解析:[/] {url}")

    with console.status("[yellow]正在解析...[/]", spinner="dots") as _status:
        result = asyncio.run(_async_parse(url))

    if result.get("status") == "error":
        console.print(f"[red]解析失败: {result.get('error')}[/]")
        return

    # Build summary table
    table = Table(title=f"页面结构 — [link={url}]{url}[/]", box=box.ROUNDED)
    table.add_column("属性", style="cyan", no_wrap=True)
    table.add_column("值", style="white")

    table.add_row("状态码", str(result.get("status_code", "?")))
    table.add_row("HTML 大小", f"{result.get('html_size', 0):,} B")
    table.add_row("标题", result.get("title") or "[dim]无[/]")
    table.add_row("H1", result.get("h1") or "[dim]无[/]")
    table.add_row("描述", (result.get("description") or "[dim]无[/]")[:80])
    table.add_row("关键词", result.get("keywords") or "[dim]无[/]")
    table.add_row("链接数", str(result.get("link_count", 0)))
    table.add_row("正文长度", f"{result.get('text_length', 0):,} 字符")

    console.print()
    console.print(table)
    console.print()


def _cmd_engines(args: list[str] | None = None) -> None:
    """engines — 列出所有可用引擎。"""
    table = Table(title="可用引擎", box=box.ROUNDED)
    table.add_column("引擎名", style="green", no_wrap=True)
    table.add_column("说明", style="white")
    table.add_column("标签", style="dim", no_wrap=True)

    for name, desc, tags in _ENGINE_INFO:
        table.add_row(name, desc, tags)

    console.print()
    console.print(table)
    console.print()


def _cmd_config(args: list[str] | None = None) -> None:
    """config — 显示当前配置。"""
    try:
        settings = Settings()
        data = settings.model_dump()
    except Exception as e:
        console.print(f"[red]加载配置失败: {e}[/]")
        return

    # Mask sensitive values
    if "llm" in data and isinstance(data["llm"], dict):
        data["llm"]["api_key"] = "***" if data["llm"].get("api_key") else ""
    if "cache" in data and isinstance(data["cache"], dict):
        data["cache"]["redis_url"] = "***" if data["cache"].get("redis_url") else ""

    import json
    config_json = json.dumps(data, indent=2, default=str, ensure_ascii=False)
    syntax = Syntax(config_json, "json", theme="monokai", line_numbers=True)

    console.print()
    console.print(Panel(syntax, title="[bold]当前配置[/]", border_style="cyan"))
    console.print()


def _cmd_history(args: list[str] | None = None) -> None:
    """history — 显示命令历史。"""
    hist_len = readline.get_current_history_length()
    if hist_len == 0:
        console.print("[yellow]没有历史命令。[/]")
        return

    console.print()
    console.print("[bold]命令历史:[/]")
    for i in range(1, hist_len + 1):
        item = readline.get_history_item(i)
        if item:
            console.print(f"  [cyan]{i:>4}[/]  {item}")
    console.print()


def _cmd_help(args: list[str] | None = None) -> None:
    """help — 显示帮助信息。"""
    console.print(Panel(_HELP_TEXT.strip(), border_style="cyan"))


# ── 主 Shell 类 ─────────────────────────────────────────────

class InteractiveShell:
    """ApexCrawler 交互式 Shell — 类似 IPython 的即时爬虫测试环境。"""

    PROMPT = "[bold cyan]apex[/] [bold]>[/] "

    # Command dispatch table
    COMMANDS: dict[str, tuple[str, object, str]] = {
        "crawl":   ("crawl <url>",              _cmd_crawl,   "测试爬取"),
        "extract": ("extract <url> [-s sel]",   _cmd_extract, "测试内容提取"),
        "parse":   ("parse <url>",              _cmd_parse,   "解析页面结构"),
        "engines": ("engines",                  _cmd_engines, "列出可用引擎"),
        "config":  ("config",                   _cmd_config,  "查看当前配置"),
        "history": ("history",                  _cmd_history, "显示命令历史"),
        "help":    ("help",                     _cmd_help,    "显示帮助"),
    }

    def __init__(self) -> None:
        self._setup_readline()

    @staticmethod
    def _setup_readline() -> None:
        """Configure readline for history support."""
        histfile = Path.home() / ".apexcrawler_history"
        try:
            readline.read_history_file(str(histfile))
        except (FileNotFoundError, OSError):
            pass
        readline.set_history_length(1000)
        import atexit
        atexit.register(lambda: readline.write_history_file(str(histfile)))

    def run(self) -> None:
        """Start the interactive shell loop."""
        console.print()
        console.print(Panel(
            "[bold cyan]ApexCrawler Interactive Shell[/]\n"
            "输入 [green]help[/] 查看可用命令, [green]exit[/] 或 [green]quit[/] 退出, "
            "Ctrl+D 快速退出",
            border_style="cyan",
        ))
        console.print()

        while True:
            try:
                line = console.input(self.PROMPT).strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            if not line:
                continue

            # Dispatch command
            self._dispatch(line)

    def _dispatch(self, line: str) -> None:
        """Parse and dispatch a command line."""
        try:
            parts = shlex.split(line)
        except ValueError as e:
            console.print(f"[red]解析错误: {e}[/]")
            return

        cmd = parts[0].lower()
        args = parts[1:]

        if cmd in ("exit", "quit"):
            console.print("[dim]再见![/]")
            sys.exit(0)

        if cmd in self.COMMANDS:
            try:
                _usage, func, _desc = self.COMMANDS[cmd]
                func(args)
            except Exception as e:
                console.print(f"[red]命令出错: {e}[/]")
        else:
            console.print(
                f"[red]未知命令: {cmd}[/]\n"
                f"  输入 [green]help[/] 查看可用命令"
            )


def run_shell() -> None:
    """Entry point for 'apex shell' command."""
    InteractiveShell().run()
