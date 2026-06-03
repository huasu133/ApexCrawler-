"""CLI entry point for ApexCrawler.

Supports:
    apex crawl <url>              Single URL crawl
    apex crawl --batch <file>     Batch crawl from file
    apex visual <url>             Visual point-and-click selector
    apex template list            List saved templates
    apex template use <name>      Use a saved template
    apex ask "<question>"         Natural language scraping
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


@click.group()
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=True), default="INFO", help="Log level (DEBUG, INFO, WARNING, ERROR)")
@click.option("--json-log", is_flag=True, default=False, help="Output logs as JSON")
@click.pass_context
def cli(ctx: click.Context, log_level: str, json_log: bool) -> None:
    """ApexCrawler — Adaptive web scraping framework with anti-crawl evasion."""
    from ..utils.logger import configure_logging
    configure_logging(level=log_level, json_format=json_log)
    ctx.ensure_object(dict)
    ctx.obj["log_level"] = log_level


# ── crawl command ──────────────────────────────────────────

@cli.command()
@click.argument("url", required=False)
@click.option("--batch", "-b", "batch_file", type=click.Path(exists=True), help="File with URLs (one per line)")
@click.option("--output", "-o", "output_file", type=click.Path(), help="Output file for results (JSON)")
@click.option("--schema", "-s", "schema_name", default="generic", help="Extraction schema (product, article, etc.)")
@click.option("--engine", "-e", "engine_name", default="", help="Force a specific browser engine")
@click.option("--proxy", "-p", "proxy_url", default="", help="Force a specific proxy")
@click.option("--geo", "-g", "geo_code", default="", help="Force proxy geo location")
@click.option("--timeout", "-t", type=click.IntRange(min=1), default=30, help="Request timeout in seconds")
@click.option("--retries", "-r", type=click.IntRange(min=1), default=3, help="Max retry attempts")
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
) -> None:
    """Crawl a single URL or batch of URLs.

    \b
    Examples:
        apex crawl https://example.com
        apex crawl --batch urls.txt -o results.json
        apex crawl https://shop.com/product/1 -s product -e cloaked
    """
    if not url and not batch_file:
        raise click.UsageError("Either URL argument or --batch/-b is required")

    urls: list[str] = []
    if url:
        urls.append(url)
    if batch_file:
        with open(batch_file) as f:
            urls.extend(line.strip() for line in f if line.strip())

    if not urls:
        click.echo("No URLs to crawl.", err=True)
        sys.exit(1)

    # Load settings
    try:
        settings = Settings()
    except Exception as e:
        click.echo(f"Configuration error: {e}", err=True)
        sys.exit(1)

    click.echo(f"ApexCrawler starting: {len(urls)} URL(s)")
    click.echo(f"  Schema: {schema_name}")
    if engine_name:
        click.echo(f"  Engine: {engine_name}")
    if proxy_url:
        click.echo(f"  Proxy: {proxy_url}")
    if geo_code:
        click.echo(f"  Geo: {geo_code}")

    async def _run():
        results = []
        from ..core.context import PipelineContext
        from ..extraction.schema import get_schema
        from ..pipeline.stages import (
            ScheduleStage, RouteStage, EvadeStage,
            ExtractStage, ValidateStage, StoreStage,
        )
        from ..pipeline.core import PipelineExecutor, StageConfig
        from ..behavior.timing import TimingScheduler
        from ..pipeline.session_manager import SessionManager
        from ..pipeline.rate_controller import RateController
        from ..http.connection_pool import ConnectionReuseManager
        from ..http.tls_router import TLSRouter

        schema = get_schema(schema_name)

        # Build shared services
        session_mgr = SessionManager()
        rate_ctrl = RateController()
        tls_router = TLSRouter()

        for idx, target_url in enumerate(urls, 1):
            click.echo(f"\n[{idx}/{len(urls)}] Crawling: {target_url}")
            try:
                _validate_url(target_url)

                ctx_obj = PipelineContext(
                    target_url=target_url,
                    extraction_schema=schema,
                )

                # Override engine if specified
                if engine_name:
                    ctx_obj.selected_engine = engine_name

                # Build pipeline stages
                timing = TimingScheduler(rate_controller=rate_ctrl)
                stages = [
                    ScheduleStage(timing=timing),
                    RouteStage(),
                    EvadeStage(router=tls_router, proxies=[proxy_url] if proxy_url else []),
                    ExtractStage(
                        engine_factory=None,
                        conn_manager=None,
                    ),
                    ValidateStage(),
                    StoreStage(),
                ]
                configs = {
                    "extract": StageConfig(timeout=timeout),
                    "schedule": StageConfig(timeout=10),
                }
                executor = PipelineExecutor(stages, configs, settings=settings)
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

            except Exception as e:
                click.echo(f"  Error: {e}", err=True)
                results.append({"url": target_url, "error": str(e)})

        # Output
        if output_file:
            output_path = Path(output_file)
            output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
            click.echo(f"\nResults written to: {output_path}")
        else:
            click.echo(f"\nResults: {json.dumps(results, indent=2, ensure_ascii=False)}")

    asyncio.run(_run())


# ── visual command ────────────────────────────────────────

@cli.command()
@click.argument("url")
@click.pass_context
def visual(ctx: click.Context, url: str) -> None:
    """Launch visual point-and-click selector for a URL.

    Opens browser with injected sidebar. Click page elements
    to define extraction fields, then export as template.

    Example:
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

@cli.group()
def template() -> None:
    """Manage extraction templates."""
    pass


@template.command("list")
def template_list() -> None:
    """List saved extraction templates."""
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
    """Crawl URL using a saved template.

    Example:
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
    """Delete a saved template."""
    from ..visual.recorder import TemplateStore

    store = TemplateStore()
    if store.delete(name):
        click.echo(f"Deleted: {name}")
    else:
        click.echo(f"Template '{name}' not found.", err=True)


# ── ask command ────────────────────────────────────────────

@cli.command()
@click.argument("query")
@click.option("--output", "-o", help="Output file (CSV/JSON)")
@click.option("--live/--no-live", default=True, help="Show live extraction progress")
@click.pass_context
def ask(ctx: click.Context, query: str, output: str | None, live: bool) -> None:
    """Natural language web scraping. Just say what you want.

    Auto-detects: URL, fields, best engine, best proxy strategy.
    Zero configuration needed.

    Examples:
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

        # Phase 2: HTTP failed, try browser
        click.echo(f"  🌐 HTTP 层提取不足，启动浏览器 ({engine})...")
        # TODO: Full browser pipeline integration
        click.echo(f"  ℹ️  浏览器管线集成开发中，当前返回页面概览")

        elapsed = time.monotonic() - start
        click.secho(f"\n✅ 分析完成 ({elapsed:.1f}s)", fg="green", bold=True)
        click.echo("─" * 50)
        click.echo(f"  Trace: {ctx_obj.trace_id}")
        click.echo(f"  提示: 使用 'apex visual {url}' 可视化点选精确字段")
        return {}

    asyncio.run(_run())


async def _try_http_extract(url: str, hints: dict) -> dict | None:
    """Try lightweight HTTP extraction with curl_cffi."""
    try:
        import httpx
    except ImportError:
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
    except Exception:
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

    return extracted if extracted else None


# ── dashboard command ──────────────────────────────────────

@cli.command()
@click.option("--port", "-p", default=8000, help="Web dashboard port")
@click.option("--open/--no-open", default=True, help="Open browser automatically")
@click.pass_context
def dashboard(ctx: click.Context, port: int, open: bool) -> None:
    """Start web dashboard for non-technical users.

    Opens a browser-based interface where anyone can:
    - Type what they want in plain language
    - See live extraction results
    - Manage templates visually
    - No terminal needed

    Example:
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
.spinner{display:none;text-align:center;padding:20px}
.spinner.active{display:block}
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
  <div class="result" id="result">
    <h3>📊 提取结果</h3>
    <div id="fields"></div>
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
    const fieldsDiv = document.getElementById('fields');
    fieldsDiv.innerHTML = Object.entries(data.data||{}).map(([k,v]) =>
      `<div class="field"><div class="field-label">${k}</div><div class="field-value">${v}</div></div>`
    ).join('') || '<div style="color:#888">未提取到数据。试试更具体的查询，或直接用 URL。</div>';
    document.getElementById('result').classList.add('visible');
  } catch(e) {
    document.getElementById('fields').innerHTML = `<div style="color:#e94560">请求失败: ${e.message}</div>`;
    document.getElementById('result').classList.add('visible');
  } finally {
    document.getElementById('submit').disabled = false;
    document.getElementById('spinner').classList.remove('active');
  }
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


# ── config command ─────────────────────────────────────────

@cli.group()
def config() -> None:
    """Manage ApexCrawler configuration."""
    pass


@config.command("show")
@click.option("--full", is_flag=True, default=False, help="Show full configuration including secrets")
@click.pass_context
def config_show(ctx: click.Context, full: bool) -> None:
    """Display current configuration."""
    try:
        settings = Settings()
        data = settings.model_dump()

        if not full:
            # Mask sensitive values
            data["llm"] = {k: ("***" if k == "api_key" and v else v) for k, v in data.get("llm", {}).items()}
            data["cache"] = {k: ("***" if k == "redis_url" and v else v) for k, v in data.get("cache", {}).items()}

        click.echo(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    except Exception as e:
        click.echo(f"Error loading config: {e}", err=True)
        sys.exit(1)


@config.command("validate")
@click.pass_context
def config_validate(ctx: click.Context) -> None:
    """Validate configuration and check for common issues."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        settings = Settings()
    except Exception as e:
        click.echo(f"CONFIGURATION ERROR: {e}", err=True)
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


# ── version command ────────────────────────────────────────

@cli.command()
def version() -> None:
    """Show ApexCrawler version."""
    from .. import __version__
    click.echo(f"ApexCrawler v{__version__}")
    click.echo("Python: " + sys.version.split()[0])
    click.echo("Platform: " + sys.platform)


# ── Entry point ────────────────────────────────────────────

def main() -> None:
    """Main entry point for the apex CLI."""
    cli(prog_name="apex")


if __name__ == "__main__":
    main()
