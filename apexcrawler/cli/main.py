"""CLI entry point for ApexCrawler.

Supports:
    apex crawl <url>              Single URL crawl
    apex crawl --batch <file>     Batch crawl from file
    apex visual <url>             Visual point-and-click selector
    apex template list            List saved templates
    apex template use <name>      Use a saved template
    apex ask "<question>"         Natural language scraping
    apex extract <url>            One-click content extraction
    apex config show              Show current configuration
    apex config validate          Validate configuration
    apex version                  Show version info
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import socket
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import click

from apexcrawler.novel.engine import NovelEngine
from ..config.schema import Settings
from ..core.exceptions import ConfigurationError

logger = logging.getLogger(__name__)

# SSRF protection: blocked private/internal IP ranges
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
]


def _format_error(message: str) -> str:
    """Format error messages with actionable fixes."""
    import re
    patterns = [
        (r"No module named '([^']+)'", r"Missing dependency: \1\n  Fix: pip install \1"),
        (r"ModuleNotFoundError: No module named '([^']+)'", r"Missing dependency: \1\n  Fix: pip install \1"),
    ]
    for pattern, replacement in patterns:
        if re.search(pattern, message, re.I):
            return re.sub(pattern, replacement, message, re.I)
    return message


def _validate_url(url: str) -> str:
    """Validate URL for SSRF protection.

    Only allows http/https schemes and blocks requests to internal/private IPs.
    Returns the validated URL or raises ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"URL scheme '{parsed.scheme}' is not allowed. Only http/https are supported."
        )
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Invalid URL: no hostname found in '{url}'")

    # Resolve hostname to IP address
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        # Not a literal IP — resolve via DNS
        try:
            resolved = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(resolved)
        except (socket.gaierror, ValueError):
            raise ValueError(f"Cannot resolve hostname: {hostname}")

    # Block internal/private IP ranges
    for net in _BLOCKED_NETWORKS:
        if ip in net:
            raise ValueError(
                f"URL targets internal/private network ({net}), blocked for security."
            )
    return url


def _show_welcome():
    """Display welcome banner with quick-start examples."""
    click.echo()
    click.echo("  ApexCrawler v0.1.0 — 自适应网页爬虫框架")
    click.echo("  " + "=" * 40)
    click.echo()
    click.echo("  快速开始:")
    click.echo(f"    apex get https://example.com                 获取页面内容")
    click.echo(f"    apex get https://example.com -o text         获取纯文本")
    click.echo(f"    apex view https://example.com                截图查看页面")
    click.echo(f"    apex save https://example.com                下载保存")
    click.echo()
    click.echo(f"  高级:")
    click.echo(f"    apex get https://example.com --engine cloaked_v2  隐身爬取")
    click.echo(f"    apex dashboard                                   启动面板")
    click.echo(f"    apex ask '爬取产品信息'                           自然语言")
    click.echo(f"    apex --help                                      全部命令")
    click.echo()


@click.group(invoke_without_command=True)
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=True), default="WARNING", help="日志级别 (--quiet=WARNING, --verbose=INFO, --log-level DEBUG=DEBUG)")
@click.option("--json-log", is_flag=True, default=False, help="以 JSON 格式输出日志")
@click.option("--quiet", is_flag=True, default=False, help="静默模式，仅显示错误")
@click.option("--verbose", is_flag=True, default=False, help="详细模式，显示调试信息")
@click.pass_context
def cli(ctx: click.Context, log_level: str, json_log: bool, quiet: bool, verbose: bool) -> None:
    """ApexCrawler — 自适应网页爬虫框架。"""
    # Show quick-start if no command
    if ctx.invoked_subcommand is None:
        _show_welcome()
        ctx.exit()

    from ..utils.logger import configure_logging
    # Suppress logging noise during --help output
    if any(arg in sys.argv for arg in ("--help", "-h")):
        log_level = "ERROR"
        json_log = False
    if quiet:
        log_level = "WARNING"
    elif verbose:
        log_level = "INFO"
    configure_logging(level=log_level, json_format=json_log)
    ctx.ensure_object(dict)
    ctx.obj["log_level"] = log_level
    ctx.obj["quiet"] = quiet
    ctx.obj["verbose"] = verbose


# ── crawl command ──────────────────────────────────────────

@cli.command(hidden=True)
@click.argument("url", required=False)
@click.option("--batch", "-b", "batch_file", type=click.Path(exists=True), help="包含 URL 的文件（每行一个）")
@click.option("--output", "-o", "output_file", type=click.Path(), help="结果输出文件（JSON 格式）")
@click.option("--schema", "-s", "schema_name", default="generic", help="提取模式（product, article 等）")
@click.option("--engine", "-e", "engine_name", default="", help="强制指定浏览器引擎")
@click.option("--proxy", "-p", "proxy_url", default="", help="强制指定代理")
@click.option("--geo", "-g", "geo_code", default="", help="强制指定代理地理位置")
@click.option("--timeout", "-t", type=click.IntRange(min=1), default=30, help="请求超时时间（秒）")
@click.option("--retries", "-r", type=click.IntRange(min=1), default=3, help="最大重试次数")
@click.option("--fast", is_flag=True, default=False, help="跳过人类行为模拟延迟（更快但更容易被检测）")
@click.option("--markdown", "markdown_output", is_flag=True, default=False, help="输出为干净的 Markdown 格式（使用 Crawl4AI 提纯）")
@click.option("--json/--no-json", "json_output", default=False, help="输出 JSON 格式结果（默认直接显示内容）")
@click.option("--quiet", is_flag=True, default=False, help="静默模式，仅显示错误", hidden=True)
@click.option("--verbose", is_flag=True, default=False, help="详细模式，显示调试信息", hidden=True)
@click.option("--resume", is_flag=True, default=False, help="从上次中断的检查点恢复爬取")
@click.option("--checkpoint-dir", type=click.Path(), default=None, help="检查点存储目录")
@click.pass_context
def crawl(
    ctx: click.Context,
    url: Optional[str],
    batch_file: Optional[str],
    output_file: Optional[str],
    schema_name: str,
    engine_name: str,
    proxy_url: str,
    geo_code: str,
    timeout: int,
    retries: int,
    fast: bool,
    markdown_output: bool,
    json_output: bool,
    quiet: bool,
    verbose: bool,
    resume: bool,
    checkpoint_dir: Optional[str],
) -> None:
    """爬取单个 URL 或批量 URL。

    支持完整 6 阶段管线：调度 → 路由 → 隐身 → 提取 → 验证 → 存储。

    \b
    示例:
        apex crawl https://example.com
        apex crawl --batch urls.txt
        apex crawl --batch urls.txt -o results.json
        apex crawl https://shop.com/product/1 -s product -e cloaked
        apex crawl --resume  # 从上次检查点恢复
        apex crawl https://example.com --checkpoint-dir /tmp/apex_cps
    """
    if not url and not batch_file and not resume:
        raise click.UsageError("Either URL argument, --batch/-b, or --resume is required")

    urls: list[str] = []
    if url:
        urls.append(url)
    if batch_file:
        with open(batch_file) as f:
            urls.extend(line.strip() for line in f if line.strip())

    if not urls:
        click.echo("No URLs to crawl.", err=True)
        sys.exit(1)

    import os
    # 清除系统代理环境变量，防止干扰爬取请求
    for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
        os.environ.pop(_k, None)

    # Load settings from YAML (fallback to env vars)
    try:
        settings = Settings.from_yaml()
    except Exception as e:
        click.echo(_format_error(f"Configuration error: {e}"), err=True)
        sys.exit(1)

    # 快速模式下禁用代理
    if fast:
        proxy_url = ""
        geo_code = ""

    click.echo(f"ApexCrawler starting: {len(urls)} URL(s)")
    click.echo(f"  Schema: {schema_name}")
    if engine_name:
        click.echo(f"  Engine: {engine_name}")
    if proxy_url:
        click.echo(f"  Proxy: {proxy_url}")
    if geo_code:
        click.echo(f"  Geo: {geo_code}")
    if fast:
        click.echo(f"  Mode: fast (no proxy, minimal delays)")

    # Apply quiet/verbose from command or group context
    if not quiet:
        quiet = ctx.obj.get("quiet", False)
    if not verbose:
        verbose = ctx.obj.get("verbose", False)
    if quiet:
        from ..utils.logger import configure_logging
        configure_logging(level="WARNING", json_format=False)
    elif verbose:
        from ..utils.logger import configure_logging
        configure_logging(level="DEBUG", json_format=False)

    async def _run():
        results = []
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
        # 导入引擎模块，触发 @EngineRegistry.register 装饰器自动注册
        from ..engines import vanilla, cloaked, camouflaged, patched, cloaked_v2

        schema = get_schema(schema_name)

        # Build shared services
        session_mgr = SessionManager()
        rate_ctrl = RateController()
        conn_mgr = ConnectionReuseManager()
        tls_router = TLSRouter()
        # 浏览器引擎池 — 自动发现已注册的引擎（Vanilla、Cloaked、Camoufox、Patched）
        engine_pool = EnginePool()

        for idx, target_url in enumerate(urls, 1):
            click.echo(f"\n[{idx}/{len(urls)}] Crawling: {target_url}")
            try:
                _validate_url(target_url)

                ctx_obj = PipelineContext(
                    target_url=target_url,
                    extraction_schema=schema,
                )

                # Engine selection: CLI arg > fast mode default
                if engine_name:
                    ctx_obj.selected_engine = engine_name
                elif fast:
                    ctx_obj.selected_engine = "vanilla"
                else:
                    ctx_obj.selected_engine = "vanilla"

                # Assemble proxy list from CLI args
                proxies = []
                if proxy_url:
                    proxies = [proxy_url]

                # Build pipeline stages
                timing = TimingScheduler()
                if fast:
                    # 快速模式：跳过拟人化定时延迟，适合测试
                    class _FastTiming:
                        _page_count = 0
                        def compute_delay(self, **kw): return 0.5
                        def reset(self): pass
                    timing = _FastTiming()

                stages = [
                    ScheduleStage(timing=timing),
                    RouteStage(),
                    EvadeStage(router=tls_router, proxies=proxies),
                    ExtractStage(
                        engine_factory=engine_pool,
                        conn_manager=None,
                    ),
                    FontDecodeStage(),
                    ValidateStage(),
                    StoreStage(),
                ]
                configs = {
                    "extract": StageConfig(timeout=timeout, retry=RetryPolicy(max_retries=retries)),
                    "schedule": StageConfig(timeout=10 if fast else timeout * 3, retry=RetryPolicy(max_retries=0 if fast else 2)),
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
                    checkpoint_dir=checkpoint_dir,
                )

                if resume:
                    # 检查点续爬模式
                    from ..pipeline.checkpoint import CheckpointManager
                    cp_mgr = CheckpointManager(
                        storage_dir=checkpoint_dir or ".apex_checkpoints"
                    )
                    checkpoints = cp_mgr.list_checkpoints()
                    if not checkpoints:
                        click.echo("No checkpoints found to resume.", err=True)
                        sys.exit(1)

                    latest = checkpoints[0]
                    job_id = f"{latest['trace_id']}_{latest['stage']}"
                    click.echo(
                        f"  Resuming from trace={latest['trace_id']} "
                        f"stage={latest['stage']} "
                        f"(saved at {latest['timestamp_iso']})"
                    )

                    # 恢复上下文
                    from ..core.context import PipelineContext
                    from ..pipeline.checkpoint import _dict_to_context
                    cp_data = cp_mgr.load(job_id)
                    if cp_data is None:
                        click.echo(
                            f"Failed to load checkpoint: {job_id}", err=True
                        )
                        sys.exit(1)

                    restored_ctx = _dict_to_context(
                        cp_data["context"], PipelineContext
                    )
                    ok, result = await executor.resume(job_id, restored_ctx)
                else:
                    ok, result = await executor.run(ctx_obj)

                duration = result.duration()
                status = "success" if ok else "failed"
                click.echo(
                    f"  [{status.upper()}] trace={result.trace_id} "
                    f"engine={result.selected_engine} "
                    f"html={len(result.raw_html or '')}B "
                    f"duration={duration:.2f}s "
                    f"valid={result.validation_passed}"
                )

                results.append({
                    "url": target_url,
                    "trace_id": result.trace_id,
                    "status": status,
                    "engine": result.selected_engine,
                    "html_bytes": len(result.raw_html or ""),
                    "duration_s": round(duration, 2),
                    "valid": result.validation_passed,
                    "stored_id": result.stored_id,
                    "errors": result.validation_errors,
                })
                # Store raw content for output
                results[-1]["raw_html"] = result.raw_html or ""
                if markdown_output:
                    results[-1]["raw_crawl4ai"] = getattr(result, "raw_crawl4ai", "") or ""

            except ValueError as e:
                click.echo(f"  [SSRF blocked] {e}", err=True)
                results.append({"url": target_url, "error": f"SSRF blocked: {e}"})
            except Exception as e:
                click.echo(_format_error(f"  Error: {e}"), err=True)
                results.append({"url": target_url, "error": str(e)})

        # Output
        if output_file:
            # Save to file (always JSON)
            output_path = Path(output_file)
            output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
            click.echo(f"\nResults written to: {output_path}")
        elif json_output:
            # JSON output mode
            click.echo(f"\nResults: {json.dumps(results, indent=2, ensure_ascii=False)}")
        else:
            # Default: show page content directly
            for r in results:
                error = r.get("error", "")
                if error:
                    click.echo(f"Error: {error}")
                    continue

                if markdown_output:
                    # Try to show Crawl4AI markdown content
                    content = r.get("raw_crawl4ai", "") or r.get("raw_html", "")
                else:
                    # Show raw HTML content
                    content = r.get("raw_html", r.get("html", ""))

                if content:
                    click.echo(content[:20000])
                else:
                    click.echo(f"Crawled: {r.get('url', '')} ({r.get('html_bytes', 0)} bytes)")

        # 关闭所有引擎实例
        await engine_pool.close_all()

    asyncio.run(_run())


# ── visual command ────────────────────────────────────────

@cli.command()
@click.argument("url")
@click.pass_context
def visual(ctx: click.Context, url: str) -> None:
    """启动可视化点选选择器，通过点击页面元素定义提取字段。

    打开浏览器并注入侧边栏。点击页面元素即可定义提取字段，
    完成后可导出为提取模板。

    \b
    示例:
        apex visual https://example.com/products
    """
    async def _run():
        from ..visual.selector import VisualSelector
        from ..visual.recorder import Template, TemplateField, TemplateStore

        selector = VisualSelector(headless=False)
        template = await selector.start(url)

        if not template.fields:
            click.echo("No fields selected. Exiting.")
            return

        click.echo(f"\n✅ {len(template.fields)} field(s) selected:\n")
        for f in template.fields:
            click.echo(f"  📌 {f.name} → {f.css_selector}")
            click.echo(f"     Sample: \"{f.sample_text}\"")

        click.echo(f"\nGenerated Pydantic Schema:\n")
        click.echo(template.pydantic_schema)

        # Save as template
        name = click.prompt("\nTemplate name", default=url.split("/")[2].split(".")[-2])
        store = TemplateStore()
        t = Template(
            name=name,
            url_pattern=url,
            fields=[
                TemplateField(name=f.name, css=f.css_selector, xpath=f.xpath, sample=f.sample_text)
                for f in template.fields
            ],
            engine=template.apex_config.get("engine", "vanilla"),
            tls_profile=template.apex_config.get("tls_profile", "chrome_124"),
            pydantic_schema=template.pydantic_schema,
            description=f"Generated from {url}",
        )
        path = store.save(t)
        click.echo(f"\n✅ Template saved: {path}")

    asyncio.run(_run())


# ── template command ───────────────────────────────────────

@cli.group(hidden=True)
def template() -> None:
    """管理提取模板。"""
    pass


@template.command("list")
def template_list() -> None:
    """列出已保存的提取模板。"""
    from ..visual.recorder import TemplateStore, ensure_builtin_templates

    ensure_builtin_templates()
    store = TemplateStore()
    names = store.list_all()

    if not names:
        click.echo("No templates found. Use 'apex visual <url>' to create one.")
        return

    click.echo(f"\n{len(names)} template(s):\n")
    for name in sorted(names):
        t = store.load(name)
        if t:
            fields_str = ", ".join(f.name for f in t.fields[:3])
            if len(t.fields) > 3:
                fields_str += f" +{len(t.fields) - 3} more"
            click.echo(f"  📋 {t.name}")
            click.echo(f"     URL: {t.url_pattern}")
            click.echo(f"     Fields: {fields_str}")
            click.echo(f"     Engine: {t.engine} | Tags: {', '.join(t.tags) or 'none'}")
            click.echo()


@template.command("use")
@click.argument("name")
@click.argument("url")
@click.option("--output", "-o", help="Output file (JSON)")
@click.pass_context
def template_use(ctx: click.Context, name: str, url: str, output: str | None) -> None:
    """使用已保存的模板爬取 URL。

    \b
    示例:
        apex template use "Amazon Product" https://amazon.com/dp/B0EXAMPLE
    """
    from ..visual.recorder import TemplateStore, ensure_builtin_templates

    ensure_builtin_templates()
    store = TemplateStore()
    template = store.load(name)

    if not template:
        click.echo(f"Template '{name}' not found. Use 'apex template list' to see available templates.", err=True)
        return

    click.echo(f"Using template: {template.name}")
    click.echo(f"  Engine: {template.engine}")
    click.echo(f"  Fields: {', '.join(f.name for f in template.fields)}")

    async def _run():
        from ..core.context import PipelineContext

        ctx_obj = PipelineContext(
            target_url=url,
        )
        click.echo(f"  Trace ID: {ctx_obj.trace_id}")
        click.echo(f"  Template fields ready for extraction (pipeline integration pending)")

        # Build extraction preview
        for f in template.fields:
            click.echo(f"    {f.name}: {f.css}")

        if output:
            import json
            Path(output).write_text(json.dumps({
                "template": template.name,
                "url": url,
                "fields": [{"name": f.name, "selector": f.css} for f in template.fields],
            }, indent=2, ensure_ascii=False))
            click.echo(f"\nConfig written to: {output}")

    asyncio.run(_run())


@template.command("delete")
@click.argument("name")
def template_delete(name: str) -> None:
    """删除已保存的模板。"""
    from ..visual.recorder import TemplateStore

    store = TemplateStore()
    if store.delete(name):
        click.echo(f"Deleted: {name}")
    else:
        click.echo(f"Template '{name}' not found.", err=True)


# ── ask command ────────────────────────────────────────────

@cli.command()
@click.argument("query")
@click.option("--output", "-o", help="输出文件（CSV/JSON）")
@click.option("--live/--no-live", default=True, help="显示实时提取进度")
@click.pass_context
def ask(ctx: click.Context, query: str, output: str | None, live: bool) -> None:
    """自然语言爬取 — 只需描述你想提取什么。

    自动检测：目标 URL、提取字段、最佳引擎和代理策略。
    零配置即可使用。

    \b
    示例:
        apex ask "提取 huaspeed.cc 的套餐价格和功能"
        apex ask "get iPhone 15 prices from amazon.com"
        apex ask "浙江大学的地址和电话"
    """
    import re
    import time

    click.secho(f"\n🔍 ApexCrawler Ask: \"{query}\"", fg="cyan", bold=True)
    click.echo("─" * 50)

    # ── Step 1: Parse query ──
    urls = re.findall(r'https?://[^\s"]+', query)
    if not urls:
        # Auto-detect bare domains like "huaspeed.cc" or "amazon.com"
        bare = re.findall(r'\b([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}(?:/[^\s]*)?)\b', query)
        if bare:
            urls = [f"https://{bare[0]}"]
    if not urls:
        # Try domain inference
        domain_hints = {
            "amazon": "https://www.amazon.com/s?k=",
            "google maps": "https://www.google.com/maps/search/",
            "walmart": "https://www.walmart.com/search?q=",
            "淘宝": "https://www.taobao.com",
            "京东": "https://www.jd.com",
        }
        for hint, base_url in domain_hints.items():
            if hint in query.lower():
                search_term = query.lower().replace(hint, "").strip()
                urls = [base_url + search_term.replace(" ", "+")]
                break

    if not urls:
        click.secho("❌ 未识别到目标网站。请在查询中包含 URL，如: apex ask \"获取 https://amazon.com 的价格\"", fg="red")
        return

    url = urls[0]
    click.echo(f"  🌐 目标: {url}")

    # ── Step 2: Auto-match template ──
    from ..visual.recorder import TemplateStore, ensure_builtin_templates, BUILTIN_TEMPLATES

    ensure_builtin_templates()
    store = TemplateStore()
    template = store.match_url(url)

    if template:
        click.secho(f"  📋 匹配模板: {template.name}", fg="green")
        click.echo(f"     引擎: {template.engine}  |  代理: {template.proxy_type}")
    else:
        click.echo(f"  📋 无匹配模板，使用通用提取模式")

    # ── Step 3: Auto-detect fields ──
    field_keywords = {
        "title": ["title", "标题", "名称", "名字", "name", "商品", "product", "item"],
        "price": ["price", "价格", "价钱", "cost", "amount", "多少钱", "费用"],
        "rating": ["rating", "评分", "评价", "review", "stars", "星级"],
        "description": ["description", "描述", "简介", "detail", "内容", "content"],
        "address": ["address", "地址", "location", "位置", "在哪里"],
        "phone": ["phone", "电话", "tel", "联系方式", "手机"],
        "image": ["image", "图片", "photo", "照片", "img"],
    }

    detected_fields = []
    for field, keywords in field_keywords.items():
        if any(kw in query.lower() for kw in keywords):
            detected_fields.append(field)

    if not detected_fields:
        detected_fields = ["title", "price"] if any(w in query.lower() for w in ["buy", "买", "shop", "价格"]) else ["title"]

    click.echo(f"  📊 检测字段: {', '.join(detected_fields)}")

    # ── Step 4: Auto-select engine ──
    engine = "vanilla"
    proxy_type = "none"

    if template:
        engine = template.engine
        proxy_type = template.proxy_type
    else:
        # Heuristic routing
        if any(d in url for d in ["amazon", "walmart", "bestbuy"]):
            engine = "cloaked"
            proxy_type = "residential"
        elif any(d in url for d in ["google.com/maps", "linkedin"]):
            engine = "camoufox"
        elif any(d in url for d in ["taobao", "jd.com", "tmall"]):
            engine = "cloaked"

    click.echo(f"  ⚙️  引擎: {engine}  |  代理: {proxy_type}")

    # ── Step 5: Execute extraction ──
    click.echo("─" * 50)
    click.secho("⏳ 开始提取...", fg="yellow")

    start = time.monotonic()

    async def _run():
        from ..core.context import PipelineContext

        ctx_obj = PipelineContext(target_url=url, trace_id="ask_" + url.split("/")[2])

        # Build extraction hints
        hints = {
            "query": query,
            "url": url,
            "detected_fields": detected_fields,
            "engine": engine,
            "proxy_type": proxy_type,
        }

        if template:
            hints["template"] = template.name
            hints["template_fields"] = [
                {"name": f.name, "css": f.css, "xpath": f.xpath}
                for f in template.fields
            ]

        # Phase 1: Use HTTP layer first (fast, lightweight)
        click.echo(f"  📡 尝试 HTTP 层提取 ({engine})...")
        extracted = await _try_http_extract(url, hints)

        if extracted:
            elapsed = time.monotonic() - start
            click.secho(f"\n✅ 提取完成! ({elapsed:.1f}s)", fg="green", bold=True)
            click.echo("─" * 50)

            for field, value in extracted.items():
                if isinstance(value, str) and len(value) > 80:
                    value = value[:80] + "..."
                click.secho(f"  {field}: ", fg="cyan", nl=False)
                click.echo(str(value))

            # Output to file
            if output:
                out_path = Path(output)
                if output.endswith(".csv"):
                    import csv
                    with open(out_path, "w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=extracted.keys())
                        writer.writeheader()
                        writer.writerow(extracted)
                else:
                    out_path.write_text(json.dumps(extracted, indent=2, ensure_ascii=False))
                click.echo(f"\n💾 已保存: {out_path}")

            return extracted

        # Phase 2: HTTP failed, try browser pipeline
        click.echo(f"  🌐 启动浏览器管线 ({engine})...")

        # Build pipeline stages
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
        from ..engines import vanilla, cloaked, camouflaged, patched, cloaked_v2

        timing = TimingScheduler()
        session_mgr = SessionManager()
        rate_ctrl = RateController()
        conn_mgr = ConnectionReuseManager()
        tls_router = TLSRouter()
        engine_pool = EnginePool()

        stages = [
            ScheduleStage(timing=timing),
            RouteStage(),
            EvadeStage(router=tls_router),
            ExtractStage(
                engine_factory=engine_pool,
                conn_manager=None,
            ),
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

        # 设置 pipeline context
        from ..core.context import PipelineContext
        from ..extraction.schema import get_schema
        schema = get_schema(hints.get('template', 'generic') or 'generic')
        ctx_obj = PipelineContext(
            target_url=url,
            extraction_schema=schema,
            selected_engine=engine,
        )

        # 清除系统代理
        import os
        for _k in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
            os.environ.pop(_k, None)

        executor = PipelineExecutor(
            stages, configs,
            settings=None,
            session_manager=session_mgr,
            rate_controller=rate_ctrl,
            degrade_manager=degrade_mgr,
            plugin_manager=plugin_mgr,
        )

        ok, result_ctx = await executor.run(ctx_obj)

        if ok and result_ctx.raw_html:
            click.echo(f"  pipeline OK, html={len(result_ctx.raw_html)}B")
            # 使用提取的数据
            if hints.get('detected_fields'):
                from ..extraction.ai_extractor import AIExtractor
                extractor = AIExtractor()
                extracted = extractor.extract_structured(result_ctx.raw_html)
                # 也尝试 LLM
                llm_result = await extractor._try_llm(result_ctx.raw_html, hints['detected_fields'], url)
                if llm_result:
                    extracted.update({k:v for k,v in llm_result.items() if v})
                return extracted
            return {"html_bytes": len(result_ctx.raw_html)}
        else:
            click.echo(f"  pipeline failed: {result_ctx.fatal_error or 'unknown'}")
            return {}

    asyncio.run(_run())


async def _try_http_extract(url: str, hints: dict) -> dict | None:
    """Try lightweight HTTP extraction with curl_cffi."""
    try:
        import httpx
    except ImportError as e:
        logger.warning(f"httpx import failed: {e}")
        return None

    # SSRF protection: validate URL before making request
    try:
        _validate_url(url)
    except ValueError:
        return None

    try:
        from urllib.parse import urlparse, urlunparse
        from apexcrawler.utils.dns_cache import dns_cache

        target_url = url
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        # DNS cache: resolve host to IP for faster connection
        parsed = urlparse(target_url)
        host = parsed.netloc.split(":")[0]
        resolved_ip = dns_cache.resolve(host)
        if resolved_ip != host:
            netloc = parsed.netloc.replace(host, resolved_ip)
            target_url = urlunparse(parsed._replace(netloc=netloc))
            headers["Host"] = host

        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(
                target_url,
                headers=headers,
            )
            html = resp.text
    except Exception as e:
        logger.warning(f"template extraction failed: {e}")
        return None

    import re
    fields = hints.get("detected_fields", [])
    template_fields = hints.get("template_fields", [])
    extracted = {}

    # Use template selectors if available
    if template_fields:
        for tf in template_fields:
            # Simple CSS selector extraction via regex fallback
            if tf.get("css"):
                # Try to extract text near the CSS selector
                pattern = re.compile(r'<[^>]*class="[^"]*' + re.escape(tf["css"].replace(".", "").replace("#", "")) + r'[^"]*"[^>]*>([^<]+)', re.IGNORECASE)
                match = pattern.search(html)
                if match:
                    extracted[tf["name"]] = match.group(1).strip()

    # Auto-extract based on field hints
    for field in fields:
        if field in extracted:
            continue

        if field == "title":
            m = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
            if m:
                extracted["title"] = m.group(1).strip()
            else:
                m = re.search(r'<h1[^>]*>([^<]+)</h1>', html, re.IGNORECASE)
                if m:
                    extracted["title"] = m.group(1).strip()

        elif field == "price":
            m = re.search(r'\$\s*([\d,.]+)', html)
            if m:
                extracted["price"] = f"${m.group(1)}"
            else:
                m = re.search(r'¥\s*([\d,.]+)', html)
                if m:
                    extracted["price"] = f"¥{m.group(1)}"

        elif field == "description":
            m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html, re.IGNORECASE)
            if m:
                extracted["description"] = m.group(1)

        elif field == "address":
            m = re.search(r'<address[^>]*>([^<]+)</address>', html, re.IGNORECASE)
            if m:
                extracted["address"] = m.group(1).strip()

        elif field == "phone":
            m = re.search(r'(tel:|电话[：:]?\s*)([\d\-+()\s]{7,20})', html)
            if m:
                extracted["phone"] = m.group(2).strip()
            else:
                m = re.search(r'([\d]{3}[\-\s]?[\d]{3,4}[\-\s]?[\d]{4})', html)
                if m:
                    extracted["phone"] = m.group(1)

    if extracted:
        extracted["_raw_html"] = html[:50000]
    return extracted if extracted else None


# ── extract command ────────────────────────────────────────

@cli.command(hidden=True)
@click.argument("url")
@click.option("--output", "-o", help="输出文件路径")
@click.option("--selector", "-s", help="CSS 选择器（如 'h1'、'.price'、'#main'）")
@click.option("--attribute", "-a", help="提取的属性（默认：文本内容）")
@click.option("--format", "-f", type=click.Choice(["txt", "md", "json"]), default="txt")
@click.option("--fast", is_flag=True, help="跳过人类行为模拟延迟")
def extract(
    url: str,
    output: Optional[str],
    selector: Optional[str],
    attribute: Optional[str],
    format: str,
    fast: bool,
) -> None:
    """一键提取网页内容 — 无需编写代码。

    使用轻量 FastFetcher（无需浏览器），支持 CSS 选择器精确提取
    或自动识别正文内容。结果可输出到文件或标准输出。

    \b
    示例:
        apex extract https://example.com
        apex extract https://example.com -s "h1" -o title.txt
        apex extract https://example.com -s "article" -f md
        apex extract https://example.com -s "a" -a href -f json
    """
    # SSRF protection
    try:
        _validate_url(url)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    # ── Fetch page ──────────────────────────────────────────
    from ..http.fetcher import FastFetcher

    click.echo(f"Fetching: {url}")
    if fast:
        click.echo("  Mode: fast (no delays)")

    fetcher = FastFetcher(timeout=30)
    try:
        result = fetcher.get(url)
    except Exception as e:
        click.echo(_format_error(f"Fetch failed: {e}"), err=True)
        sys.exit(1)
    finally:
        fetcher.close()

    if result["status_code"] != 200:
        click.echo(
            f"HTTP {result['status_code']} — expected 200",
            err=True,
        )
        if result["status_code"] == 0:
            sys.exit(1)

    html: str = result["html"]  # type: ignore[assignment]
    status = result["status_code"]

    click.echo(f"  Status: {status}  |  Size: {len(html):,} bytes")

    if not html.strip():
        click.echo("Empty response body.", err=True)
        sys.exit(1)

    # ── Extract content ─────────────────────────────────────
    extracted: list[dict[str, str]] = []

    if selector:
        # CSS selector-based extraction
        try:
            from lxml.html import fromstring
        except ImportError:
            click.echo("lxml is required for CSS selector extraction.", err=True)
            sys.exit(1)

        try:
            doc = fromstring(html)
            elements = doc.cssselect(selector)
        except Exception as e:
            click.echo(_format_error(f"Selector error: {e}"), err=True)
            sys.exit(1)

        if not elements:
            click.echo(f"No elements matched selector: {selector}", err=True)
            sys.exit(1)

        for el in elements:
            if attribute:
                value = el.get(attribute, "")
            else:
                # inner text, cleaned
                value = el.text_content().strip()
            # Keep a rough location hint
            tag = el.tag if hasattr(el, "tag") else ""
            extracted.append({"tag": tag, "text": value})
    else:
        # Auto-extract: find main content area
        try:
            from lxml.html import fromstring
        except ImportError:
            click.echo("lxml is required for content extraction.", err=True)
            sys.exit(1)

        doc = fromstring(html)
        # Try <article> first, then <main>, then <body>
        content_el = doc.cssselect("article")
        if not content_el:
            content_el = doc.cssselect("main")
        if not content_el:
            content_el = doc.cssselect("body")
        if content_el:
            raw_text = content_el[0].text_content().strip()
        else:
            raw_text = doc.text_content().strip()

        # Clean whitespace
        import re
        lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
        clean = " ".join(lines)
        clean = re.sub(r"\s{2,}", " ", clean)
        extracted = [{"tag": "content", "text": clean}]

    # ── Output ──────────────────────────────────────────────
    if format == "json":
        output_data = {
            "url": url,
            "status": status,
            "selector": selector or "auto",
            "count": len(extracted),
            "results": extracted,
        }
        output_str = json.dumps(output_data, indent=2, ensure_ascii=False)
    elif format == "md":
        lines_out: list[str] = []
        lines_out.append(f"# Extracted from {url}\n")
        for item in extracted:
            text = item["text"]
            lines_out.append(text)
            lines_out.append("")
        output_str = "\n".join(lines_out)
    else:
        # txt
        output_str = "\n\n".join(item["text"] for item in extracted)
        # Strip trailing newline
        output_str = output_str.rstrip("\n")

    # Write or print
    if output:
        out_path = Path(output)
        out_path.write_text(output_str, encoding="utf-8")
        click.echo(f"\nWritten: {out_path} ({len(output_str):,} chars)")
    else:
        click.echo("\n" + "─" * 50)
        click.echo(output_str)


# ── dashboard command ──────────────────────────────────────

@cli.command()
@click.option("--port", "-p", default=8000, help="Web 面板端口")
@click.option("--open/--no-open", default=True, help="自动打开浏览器")
@click.pass_context
def dashboard(ctx: click.Context, port: int, open: bool) -> None:
    """启动 Web 面板 — 无需终端即可爬取。

    打开浏览器界面，任何人都可以：
    - 用自然语言输入爬取需求
    - 实时查看提取结果
    - 可视化管理模板

    \b
    示例:
        apex dashboard
    """
    try:
        import uvicorn
    except ImportError:
        click.echo("Installing dashboard dependencies...")
        import subprocess, sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "uvicorn", "fastapi", "-q"])
        import uvicorn

    click.secho(f"\n🚀 ApexCrawler Dashboard", fg="cyan", bold=True)
    click.echo(f"  地址: http://localhost:{port}")
    click.echo(f"  任何人在浏览器中输入需求即可开始爬取")
    click.echo(f"  按 Ctrl+C 停止\n")

    asyncio.run(_start_dashboard(port, open))


async def _start_dashboard(port: int, open_browser: bool):
    """Start FastAPI server with simple web dashboard."""
    from fastapi import FastAPI, HTTPException, Depends
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    import uvicorn

    app = FastAPI(title="ApexCrawler Dashboard", version="0.1.0")

    # CORS middleware — restrict origins in production
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API Key authentication ──
    import os
    from fastapi import Security
    from fastapi.security import APIKeyHeader

    _API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
    _EXPECTED_API_KEY = os.environ.get("APEX_API_KEY", "")

    async def _require_api_key(api_key: str = Security(_API_KEY_HEADER)) -> None:
        if _EXPECTED_API_KEY and api_key != _EXPECTED_API_KEY:
            raise HTTPException(status_code=403, detail="Invalid or missing API key")

    class AskRequest(BaseModel):
        query: str

    DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ApexCrawler Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:system-ui,sans-serif;background:#0f0f23;color:#e0e0e0;min-height:100vh}
.header{background:#1a1a2e;padding:20px 40px;border-bottom:2px solid #e94560}
.header h1{color:#e94560;font-size:24px}
.header p{color:#888;font-size:13px;margin-top:4px}
.container{max-width:800px;margin:40px auto;padding:0 20px}
.input-box{background:#1a1a2e;border-radius:12px;padding:24px;margin-bottom:24px}
.input-box textarea{width:100%;height:80px;background:#0f0f23;border:1px solid #333;border-radius:8px;
  color:#e0e0e0;padding:12px;font-size:15px;resize:vertical;font-family:inherit}
.input-box textarea:focus{outline:none;border-color:#e94560}
.btn{width:100%;padding:14px;background:#e94560;color:#fff;border:none;border-radius:8px;
  cursor:pointer;font-size:15px;font-weight:600;margin-top:12px}
.btn:hover{background:#d63850}
.btn:disabled{opacity:0.5;cursor:not-allowed}
.result{background:#1a1a2e;border-radius:12px;padding:24px;display:none}
.result.visible{display:block}
.result h3{color:#e94560;margin-bottom:16px}
.field{margin-bottom:12px;padding:10px;background:#0f0f23;border-radius:6px}
.field-label{color:#888;font-size:11px;text-transform:uppercase}
.field-value{color:#4ecca3;font-size:14px;margin-top:4px;word-break:break-all}
.result-box{background:#1a1a2e;border-radius:12px;padding:24px;margin-top:24px;display:none}
.result-box.visible{display:block}
.tab-btn{font-size:13px;transition:all .2s}
.tab-btn:hover{color:#e94560!important}
.spinner{display:none;text-align:center;padding:20px}
.spinner.active{display:inline-block;width:20px;height:20px;border:2px solid #333;border-top-color:#e94560;border-radius:50%;animation:spin .8s linear infinite;margin-left:8px;vertical-align:middle}
@keyframes spin{to{transform:rotate(360deg)}}
.tips{background:#1a1a2e;border-radius:12px;padding:20px;margin-top:24px}
.tips h4{color:#888;margin-bottom:12px}
.tip{color:#666;font-size:12px;margin:6px 0;padding:4px 0}
.tip::before{content:"💡 ";color:#e94560}
</style>
</head>
<body>
<div class="header">
  <h1>🎛️ ApexCrawler</h1>
  <p>告诉我想提取什么，剩下的交给我</p>
</div>
<div class="container">
  <div class="input-box">
    <textarea id="query" placeholder='例如: "huaspeed.cc 的套餐价格和功能"
"amazon.com iPhone 15 价格和评分"
"浙江大学的主页地址和电话"'></textarea>
    <button class="btn" id="submit" onclick="ask()">🔍 开始爬取</button>
  </div>
  <div class="spinner" id="spinner">⏳ 分析页面中...</div>
  <div id="result" class="result-box">
    <div class="tabs" style="display:flex;gap:4px;margin-bottom:12px;border-bottom:1px solid #333;">
      <button class="tab-btn active" onclick="switchTab('content')" style="background:none;border:none;color:#e94560;padding:8px 16px;cursor:pointer;font-size:13px;border-bottom:2px solid #e94560;">内容</button>
      <button class="tab-btn" onclick="switchTab('json')" style="background:none;border:none;color:#888;padding:8px 16px;cursor:pointer;font-size:13px;">JSON</button>
      <button class="tab-btn" onclick="switchTab('raw')" style="background:none;border:none;color:#888;padding:8px 16px;cursor:pointer;font-size:13px;">原始HTML</button>
    </div>
    <div id="tab-content" style="background:#0f0f23;border-radius:8px;padding:16px;max-height:500px;overflow:auto;font-size:13px;line-height:1.6;white-space:pre-wrap;word-break:break-all;"></div>
    <div id="tab-json" style="display:none;background:#0f0f23;border-radius:8px;padding:16px;max-height:500px;overflow:auto;font-size:12px;line-height:1.5;font-family:monospace;white-space:pre;"></div>
    <div id="tab-raw" style="display:none;background:#0f0f23;border-radius:8px;padding:16px;max-height:500px;overflow:auto;font-size:11px;line-height:1.4;font-family:monospace;white-space:pre-wrap;word-break:break-all;"></div>
  </div>
  <div class="tips">
    <h4>💡 使用提示</h4>
    <div class="tip">自然语言描述需求，会自动识别目标网站和字段</div>
    <div class="tip">内置 Amazon / Google Maps 等常用站点的最佳配置</div>
    <div class="tip">自动选择最优引擎和代理策略，零配置</div>
  </div>
</div>
<script>
async function ask() {
  const query = document.getElementById('query').value.trim();
  if (!query) return;
  document.getElementById('submit').disabled = true;
  document.getElementById('spinner').classList.add('active');
  document.getElementById('result').classList.remove('visible');
  try {
    const resp = await fetch('/api/ask', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({query})
    });
    const data = await resp.json();
    
    // Show content in markdown-like format
    const contentDiv = document.getElementById('tab-content');
    const jsonDiv = document.getElementById('tab-json');
    const rawDiv = document.getElementById('tab-raw');
    
    // Content tab: show text content (from data.data or data.markdown)
    const fields = data.data || {};
    const markdown = data.markdown || '';
    let contentHtml = '';
    if (markdown) {
      contentHtml = '<div style="color:#e0e0e0;">' + markdown.replace(/\n/g, '<br>') + '</div>';
    } else {
      contentHtml = Object.entries(fields).map(([k,v]) => 
        `<div style="margin-bottom:8px;"><span style="color:#e94560;font-weight:500;">${k}</span><br><span style="color:#ccc;">${v}</span></div>`
      ).join('');
      // Exclude internal fields
      contentHtml = contentHtml.replace(/<div[^>]*>_raw_html[^<]*</div>/g, '');
    }
    if (!contentHtml) contentHtml = '<div style="color:#888;">未提取到内容</div>';
    contentDiv.innerHTML = contentHtml;
    
    // JSON tab
    jsonDiv.textContent = JSON.stringify(data, null, 2);
    
    // Raw tab: show raw HTML if available
    rawDiv.textContent = data.raw_html || '无原始 HTML';
    
    document.getElementById('result').classList.add('visible');
    switchTab('content');
  } catch(e) {
    document.getElementById('tab-content').innerHTML = '<div style="color:#e94560;">请求失败: ' + e.message + '</div>';
    document.getElementById('result').classList.add('visible');
  }
  document.getElementById('spinner').classList.remove('active');
  document.getElementById('submit').disabled = false;
}

function switchTab(name) {
  const tabs = ['content', 'json', 'raw'];
  const labels = {'content':'内容','json':'JSON','raw':'原始HTML'};
  tabs.forEach(t => {
    const el = document.getElementById('tab-' + t);
    if (el) el.style.display = t === name ? 'block' : 'none';
  });
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.style.color = btn.textContent === labels[name] ? '#e94560' : '#888';
    btn.style.borderBottom = btn.textContent === labels[name] ? '2px solid #e94560' : '2px solid transparent';
  });
}
document.getElementById('query').addEventListener('keydown', e => {
  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) ask();
});
</script>
</body></html>"""

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return DASHBOARD_HTML

    @app.post("/api/ask")
    async def api_ask(req: AskRequest, _: None = Depends(_require_api_key)):
        import re, httpx, json as json_mod
        query = req.query

        urls = re.findall(r'https?://[^\s"]+', query)
        if not urls:
            # Auto-detect bare domains like "huaspeed.cc" or "amazon.com"
            bare = re.findall(r'\b([a-zA-Z0-9][-a-zA-Z0-9]*\.[a-zA-Z]{2,}(?:/[^\s]*)?)\b', query)
            if bare:
                urls = [f"https://{bare[0]}"]
            if not urls:
                raise HTTPException(400, "查询中未包含 URL")

        url = urls[0]

        # SSRF protection: validate URL before making request
        try:
            _validate_url(url)
        except ValueError as e:
            raise HTTPException(400, str(e))

        # Try template match
        from ..visual.recorder import TemplateStore, ensure_builtin_templates
        ensure_builtin_templates()
        store = TemplateStore()
        template = store.match_url(url)

        # Detect fields
        field_keywords = {
            "title": ["title", "标题", "名称", "名字", "name", "商品"],
            "price": ["price", "价格", "价钱", "cost", "多少钱"],
            "rating": ["rating", "评分", "评价", "review", "stars"],
            "address": ["address", "地址", "location", "位置"],
            "phone": ["phone", "电话", "tel", "联系方式"],
        }
        detected_fields = [
            f for f, kws in field_keywords.items()
            if any(kw in query.lower() for kw in kws)
        ] or ["title"]

        # Execute extraction
        extracted = await _try_http_extract(url, {
            "detected_fields": detected_fields,
            "template_fields": [
                {"name": f.name, "css": f.css, "xpath": f.xpath}
                for f in template.fields
            ] if template else []
        })

        return JSONResponse({
            "query": query,
            "url": url,
            "template": template.name if template else None,
            "detected_fields": detected_fields,
            "data": extracted or {},
            "raw_html": (extracted or {}).get("_raw_html", ""),
            "markdown": (extracted or {}).get("_raw_html", ""),
        })

    @app.get("/api/templates")
    async def api_templates(_: None = Depends(_require_api_key)):
        from ..visual.recorder import TemplateStore, ensure_builtin_templates
        ensure_builtin_templates()
        store = TemplateStore()
        return [{
            "name": t.name,
            "url_pattern": t.url_pattern,
            "fields": len(t.fields),
            "engine": t.engine,
        } for t in [store.load(n) for n in store.list_all()] if t]

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    if open_browser:
        import webbrowser
        webbrowser.open(f"http://localhost:{port}")

    await server.serve()


# ── get command ────────────────────────────────────────────


@cli.command(name="get", help="获取页面内容（静默模式，直接输出）")
@click.argument("url", required=True)
@click.option("--engine", "-e", default="", help="引擎类型 (cloaked_v2, camoufox, patched, vanilla)")
@click.option("--proxy", "-p", default="", help="代理地址")
@click.option("--timeout", "-t", type=int, default=30, help="超时秒数")
@click.option("--output", "-o", type=click.Choice(["html", "text"]), default="html", help="输出格式")
@click.pass_context
def get_cmd(ctx: click.Context, url: str, engine: str, proxy: str, timeout: int, output: str) -> None:
    """快速获取页面内容。完全静默，只输出内容，不输出日志和元数据。

    示例:
        apex get https://example.com
        apex get https://example.com --engine cloaked_v2
        apex get https://example.com -o text
    """
    import re
    # Detect novel platform URLs (Qidian, Fanqie, etc.)
    novel_domains = ["qidian.com", "fanqienovel.com", "jinjiang.com", "biquge", "69shu"]
    if any(d in url for d in novel_domains):
        try:
            ne = NovelEngine()
            book = ne.info(url)
            click.echo(f"Novel: {book.book_id} ({len(book.chapters)} chapters)")
            # Show first chapter as preview
            free_chapters = [c for c in book.chapters if not c.is_vip]
            if free_chapters:
                ch = free_chapters[0]
                text = ne.chapter(ch.url if ch.url else url)
                if text:
                    click.echo(f"\n=== {ch.title} ===\n")
                    click.echo(text[:2000])
                    return
        except Exception as e:
            logger.debug("Novel detection failed: %s", e)
            # Fall through to regular get

    try:
        from apexcrawler.get import get
        content = get(url, engine=engine, proxy=proxy, timeout=timeout, output=output)
        click.echo(content, nl=False)
    except Exception as e:
        click.echo(_format_error(f"Error: {e}"), err=True)
        raise click.Abort()


# ── view command ───────────────────────────────────────────


@cli.command(name="view", help="在浏览器中查看页面并截图")
@click.argument("url", required=True)
@click.option("--engine", "-e", default="cloaked_v2", help="引擎类型")
@click.option("--output", "-o", default="", help="截图保存路径（默认自动生成）")
@click.pass_context
def view_cmd(ctx: click.Context, url: str, engine: str, output: str) -> None:
    """使用浏览器引擎渲染页面并保存截图。

    示例:
        apex view https://example.com
        apex view https://example.com --engine cloaked_v2
    """
    import asyncio
    import os

    async def _view():
        save_path = output or f"screenshot_{url.split('//')[-1].split('/')[0]}.png"

        if engine == "cloaked_v2":
            try:
                import cloakbrowser
                b = await cloakbrowser.launch_async(headless=True)
                p = await b.new_page()
                await p.goto(url, wait_until="networkidle", timeout=30000)
                await p.screenshot(path=save_path, full_page=True)
                title = await p.title()
                await b.close()
                click.echo(f"Title: {title}")
                click.echo(f"Screenshot: {os.path.abspath(save_path)}")
            except Exception as e:
                click.echo(_format_error(f"Browser error: {e}"), err=True)
                raise click.Abort()
        else:
            try:
                from playwright.async_api import async_playwright
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=True)
                    page = await browser.new_page()
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                    await page.screenshot(path=save_path, full_page=True)
                    title = await page.title()
                    await browser.close()
                    click.echo(f"Title: {title}")
                    click.echo(f"Screenshot: {os.path.abspath(save_path)}")
            except Exception as e:
                click.echo(_format_error(f"Browser error: {e}"), err=True)
                raise click.Abort()

    asyncio.run(_view())


# ── save command ───────────────────────────────────────────


@cli.command(name="save", help="下载页面内容保存到文件")
@click.argument("url", required=True)
@click.option("--output", "-o", default="", help="保存路径（默认自动推断）")
@click.option("--engine", "-e", default="", help="引擎类型")
@click.option("--format", "fmt", type=click.Choice(["html", "text"]), default="html", help="保存格式")
@click.pass_context
def save_cmd(ctx: click.Context, url: str, output: str, engine: str, fmt: str) -> None:
    """下载页面内容并保存到文件。自动推断文件名，支持 html/text 格式。

    示例:
        apex save https://example.com
        apex save https://example.com -o page.html
        apex save https://example.com --format text
    """
    import os
    from urllib.parse import urlparse

    try:
        from apexcrawler.get import get
        content = get(url, engine=engine, timeout=60, output=fmt)

        if not output:
            domain = urlparse(url).netloc or "page"
            output = f"{domain}.{'txt' if fmt == 'text' else 'html'}"

        with open(output, "w", encoding="utf-8") as f:
            f.write(content)

        size = len(content)
        click.echo(f"Saved: {os.path.abspath(output)} ({size} bytes)")
    except Exception as e:
        click.echo(_format_error(f"Error: {e}"), err=True)
        raise click.Abort()


# ── qidian command ────────────────────────────────────────

@cli.group()
def qidian() -> None:
    """起点中文网爬取工具（免费章节无需登录）。"""
    pass


@qidian.command()
@click.option("--cookie-name", default="qidian", help="Cookie 保存名称")
def login(cookie_name: str) -> None:
    """扫码登录起点 → 导出 Cookie。"""
    from ..engines.qidian import QidianEngine

    click.echo("正在启动浏览器进行扫码登录...")
    engine = QidianEngine(headless=False)
    try:
        qc = engine.login_sync()
        if qc.parsed:
            click.echo(f"登录成功，获取到 {len(qc.parsed)} 个 Cookie")
            # 转为 Playwright cookie 格式保存
            pw_cookies = [
                {"name": k, "value": v, "domain": ".qidian.com", "path": "/"}
                for k, v in qc.parsed.items()
            ]
            path = engine.save_cookies(pw_cookies, name=cookie_name)
            click.echo(f"Cookie 已保存: {path}")

            if qc.expires_at:
                expires = qc.expires_at.strftime("%Y-%m-%d %H:%M:%S")
                click.echo(f"过期时间: {expires}")
        else:
            click.echo("登录失败，未能获取到 Cookie")
    except Exception as e:
        click.echo(_format_error(f"登录失败: {e}"), err=True)
        sys.exit(1)


@qidian.command()
@click.argument("book_id", type=int)
@click.option("--cookie-name", default="qidian", help="Cookie 文件名称")
@click.option("--no-cookie", is_flag=True, help="不使用 Cookie（仅查看公开信息）")
def info(book_id: int, cookie_name: str, no_cookie: bool) -> None:
    """查看书籍信息 + 章节列表。"""
    from ..engines.qidian import QidianEngine

    engine = QidianEngine()
    try:
        # 注入 Cookie
        if not no_cookie:
            cookies = engine.load_cookies(name=cookie_name)
            if cookies:
                engine.set_cookies_from_list(cookies)
                click.echo(f"已加载 Cookie ({len(cookies)} 个)")
            else:
                click.echo(
                    "未找到有效的 Cookie，请先执行 'apex qidian login'",
                    err=True,
                )
                sys.exit(1)

        chapters = engine.fetch_catalog(book_id)
        if not chapters:
            click.echo("未获取到章节列表，请检查 book_id 或 Cookie 是否有效")
            sys.exit(1)

        vip_count = sum(1 for c in chapters if c.is_vip)
        free_count = len(chapters) - vip_count
        total_words = sum(c.word_count for c in chapters)

        click.echo(f"\n书籍 ID: {book_id}")
        click.echo(f"总章节数: {len(chapters)}")
        click.echo(f"免费章节: {free_count}")
        click.echo(f"付费章节: {vip_count}")
        click.echo(f"总字数: {total_words:,}\n")

        click.echo(f"{'序号':>4}  {'标题':<30} {'字数':>6}  {'状态':<4}  {'章节ID':<8}")
        click.echo("-" * 70)
        for ch in chapters[:50]:  # 显示前 50 章
            status = "VIP" if ch.is_vip else "免费"
            click.echo(
                f"{ch.index:>4}  {ch.title[:28]:<30} "
                f"{ch.word_count:>6}  {status:<4}  {ch.chapter_id:<8}"
            )

        if len(chapters) > 50:
            click.echo(f"\n... 共 {len(chapters)} 章，仅展示前 50 章")
    except Exception as e:
        click.echo(_format_error(f"获取信息失败: {e}"), err=True)
        sys.exit(1)


@qidian.command()
@click.argument("book_id", type=int)
@click.option("--cookie-name", default="qidian", help="Cookie 文件名称")
@click.option("--output", "-o", type=click.Choice(["json", "txt"]), default="json", help="输出格式")
@click.option("--limit", "-n", type=int, default=0, help="限制爬取章节数（0=全部）")
@click.option("--start", "-s", type=int, default=1, help="从第几章开始")
@click.option("--delay", is_flag=True, help="模拟真实阅读延迟")
@click.option("--dir", "out_dir", type=click.Path(), default=None, help="输出目录")
def crawl(
    book_id: int,
    cookie_name: str,
    output: str,
    limit: int,
    start: int,
    delay: bool,
    out_dir: Optional[str],
) -> None:
    """爬取免费章节。"""
    from ..engines.qidian import QidianEngine

    engine = QidianEngine(storage_dir=out_dir)
    try:
        # 加载 Cookie
        cookies = engine.load_cookies(name=cookie_name)
        if cookies:
            engine.set_cookies_from_list(cookies)
            click.echo(f"已加载 Cookie ({len(cookies)} 个)")
        else:
            click.echo(
                "未找到有效的 Cookie，请先执行 'apex qidian login'",
                err=True,
            )
            sys.exit(1)

        # 获取章节列表
        chapters = engine.fetch_catalog(book_id)
        if not chapters:
            click.echo("未获取到章节列表")
            sys.exit(1)

        # 筛选免费章节 + 起始偏移
        free_chapters = [c for c in chapters if not c.is_vip]
        click.echo(f"\n书籍共 {len(chapters)} 章（免费 {len(free_chapters)} 章）")

        target_chapters = free_chapters[start - 1:]
        if limit > 0:
            target_chapters = target_chapters[:limit]

        click.echo(
            f"准备爬取 {len(target_chapters)} 章 "
            f"(从第 {target_chapters[0].index} 章到第 {target_chapters[-1].index} 章)"
        )

        # 爬取正文
        click.echo("\n开始爬取...")
        results = engine.fetch_chapters(target_chapters)

        # 统计
        success = sum(1 for c in results if c.content)
        total_words = sum(c.word_count for c in results)

        click.echo(f"\n爬取完成: {success}/{len(results)} 章成功")
        click.echo(f"总字数: {total_words:,}")

        # 获取书名
        book_title = f"book_{book_id}"

        # 保存结果
        if output == "json":
            path = engine.save_book_json(book_id, book_title, results)
        else:
            path = engine.save_book_txt(book_id, book_title, results)

        click.echo(f"结果已保存: {path}")

        if delay and success > 0:
            avg_words = total_words // success
            click.echo(
                f"\n若启用阅读模拟，预计阅读时间: "
                f"{avg_words * success // 1000} 分钟"
            )

    except Exception as e:
        click.echo(_format_error(f"爬取失败: {e}"), err=True)
        sys.exit(1)


# ── config command ─────────────────────────────────────────

@cli.group(hidden=True)
def config() -> None:
    """管理 ApexCrawler 配置。"""
    pass


@config.command("show")
@click.option("--full", is_flag=True, default=False, help="显示完整配置（包括密钥）")
@click.pass_context
def config_show(ctx: click.Context, full: bool) -> None:
    """显示当前配置。"""
    try:
        settings = Settings()
        data = settings.model_dump()

        if not full:
            # Mask sensitive values
            data["llm"] = {k: ("***" if k == "api_key" and v else v) for k, v in data.get("llm", {}).items()}
            data["cache"] = {k: ("***" if k == "redis_url" and v else v) for k, v in data.get("cache", {}).items()}

        click.echo(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    except Exception as e:
        click.echo(_format_error(f"Error loading config: {e}"), err=True)
        sys.exit(1)


@config.command("validate")
@click.pass_context
def config_validate(ctx: click.Context) -> None:
    """验证配置并检查常见问题。"""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        settings = Settings()
    except Exception as e:
        click.echo(_format_error(f"CONFIGURATION ERROR: {e}"), err=True)
        sys.exit(1)

    # Validate engines
    if not settings.engines:
        errors.append("No engines configured — at least one engine is required")

    for name, engine in settings.engines.items():
        if engine.max_concurrent < 1:
            errors.append(f"Engine '{name}': max_concurrent must be >= 1")
        if engine.timeout_seconds < 5:
            warnings.append(f"Engine '{name}': timeout_seconds ({engine.timeout_seconds}) is very low")

    # Validate proxy config
    if settings.proxy.min_pool_size < 1:
        warnings.append("proxy.min_pool_size is 0 — no proxies will be used")

    if settings.proxy.health_check_interval < 10:
        warnings.append(f"proxy.health_check_interval ({settings.proxy.health_check_interval}s) is very low")

    # Validate pipeline config
    if settings.pipeline.max_concurrent_tasks < 1:
        errors.append("pipeline.max_concurrent_tasks must be >= 1")

    if settings.pipeline.retry_max < 0:
        errors.append("pipeline.retry_max cannot be negative")

    # Validate LLM config
    if settings.llm.temperature < 0 or settings.llm.temperature > 2:
        warnings.append(f"llm.temperature ({settings.llm.temperature}) is outside typical range (0.0–2.0)")

    if settings.llm.max_tokens < 100:
        warnings.append(f"llm.max_tokens ({settings.llm.max_tokens}) is very low for content extraction")

    # Output results
    if errors:
        click.secho(f"\n{len(errors)} ERROR(S):", fg="red", bold=True)
        for e in errors:
            click.secho(f"  ✗ {e}", fg="red")
    else:
        click.secho("\n✓ No errors found", fg="green")

    if warnings:
        click.secho(f"\n{len(warnings)} WARNING(S):", fg="yellow", bold=True)
        for w in warnings:
            click.secho(f"  ⚠ {w}", fg="yellow")

    if errors:
        click.echo(f"\nEngines configured: {', '.join(settings.engines.keys()) or 'none'}")
        click.echo(f"Log level: {settings.log_level}")
        sys.exit(1)

    click.echo(f"\nEngines configured: {', '.join(settings.engines.keys()) or 'none'}")
    click.echo(f"Log level: {settings.log_level}")
    click.echo(f"Pipeline concurrency: {settings.pipeline.max_concurrent_tasks}")
    click.echo(f"LLM provider: {settings.llm.provider}/{settings.llm.model}")
    click.echo("Configuration is valid.")


# ── shell command ──────────────────────────────────────────

@cli.command()
def shell() -> None:
    """进入交互式 Shell — 实时测试爬取命令。"""
    from apexcrawler.cli.shell import InteractiveShell
    InteractiveShell().run()


# ── version command ────────────────────────────────────────

@cli.command()
def version() -> None:
    """显示 ApexCrawler 版本信息。"""
    from .. import __version__
    click.echo(f"ApexCrawler v{__version__}")
    click.echo("Python: " + sys.version.split()[0])
    click.echo("Platform: " + sys.platform)


# ── checkpoints command ──────────────────────────────────

@cli.group(hidden=True)
def checkpoints() -> None:
    """管理断点续爬的检查点。"""
    pass


@checkpoints.command("list")
@click.option("--checkpoint-dir", type=click.Path(), default=None, help="检查点存储目录")
def checkpoints_list(checkpoint_dir: str | None) -> None:
    """列出所有可用的检查点。"""
    from ..pipeline.checkpoint import CheckpointManager

    mgr = CheckpointManager(storage_dir=checkpoint_dir or ".apex_checkpoints")
    entries = mgr.list_checkpoints()

    if not entries:
        click.echo("No checkpoints found.")
        return

    click.echo(f"\n{len(entries)} checkpoint(s) available:\n")
    for entry in entries:
        stage = entry.get("stage", "?")
        trace = entry.get("trace_id", "?")
        ts = entry.get("timestamp_iso", "?")
        click.echo(f"  trace={trace}  stage={stage}  saved_at={ts}")


@checkpoints.command("clear")
@click.option("--checkpoint-dir", type=click.Path(), default=None, help="检查点存储目录")
@click.option("--all", "clear_all", is_flag=True, default=False, help="清除所有检查点")
@click.argument("job_id", required=False)
def checkpoints_clear(checkpoint_dir: str | None, clear_all: bool, job_id: str | None) -> None:
    """清除检查点。提供 JOB_ID 或使用 --all 清除全部。"""
    from ..pipeline.checkpoint import CheckpointManager

    mgr = CheckpointManager(storage_dir=checkpoint_dir or ".apex_checkpoints")

    if clear_all:
        mgr.clear()
        click.echo("All checkpoints cleared.")
    elif job_id:
        mgr.clear(job_id)
        click.echo(f"Checkpoint cleared: {job_id}")
    else:
        click.echo("Specify a job_id or use --all to clear all checkpoints.", err=True)
        sys.exit(1)


# ── novel command ──────────────────────────────────────────

@cli.group(name="novel", help="小说爬取 — 支持起点/番茄/笔趣阁等站点")
def novel_group():
    """小说爬取相关命令。

    示例:
        apex novel info https://book.qidian.com/info/107580
        apex novel download https://book.qidian.com/info/107580
        apex novel download https://book.qidian.com/info/107580 --chapters 1-100
    """
    pass


@novel_group.command(name="info", help="获取小说信息和章节列表")
@click.argument("url", required=True)
def novel_info(url: str) -> None:
    """获取小说信息。

    URL 示例:
        https://book.qidian.com/info/107580
        https://fanqienovel.com/...
    """
    try:
        ne = NovelEngine()
        book = ne.info(url)
        free = sum(1 for c in book.chapters if not c.is_vip)
        click.echo(f"Book ID: {book.book_id}")
        click.echo(f"Total: {len(book.chapters)} chapters ({free} free)")
        click.echo()
        for c in book.chapters[:20]:
            tag = " " if not c.is_vip else "$"
            click.echo(f"  {c.index:4d}. [{tag}] {c.title}")
        if len(book.chapters) > 20:
            click.echo(f"  ... ({len(book.chapters) - 20} more)")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()


@novel_group.command(name="download", help="下载小说章节")
@click.argument("url", required=True)
@click.option("--chapters", "-c", default="", help="章节范围 (1-100, 50-200)")
@click.option("--output", "-o", default="", help="输出文件路径")
@click.option("--format", "fmt", type=click.Choice(["txt", "epub"]), default="txt", help="输出格式")
def novel_download(url: str, chapters: str, output: str, fmt: str) -> None:
    """下载小说章节到本地文件。

    示例:
        apex novel download https://book.qidian.com/info/107580
        apex novel download https://book.qidian.com/info/107580 -c 1-100
        apex novel download https://book.qidian.com/info/107580 -o 凡人修仙传.txt
    """
    try:
        start, end = 1, 0
        if chapters:
            parts = chapters.split("-")
            start = int(parts[0])
            if len(parts) > 1:
                end = int(parts[1])

        ne = NovelEngine()
        result = ne.download(url, start=start, end=end, output=fmt)
        click.echo(f"Saved: {result}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise click.Abort()


# ── Entry point ────────────────────────────────────────────

def main() -> None:
    """Main entry point for the apex CLI."""
    cli(prog_name="apex")


if __name__ == "__main__":
    main()
