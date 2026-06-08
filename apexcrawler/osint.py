"""ApexCrawler OSINT 模块 — 网站基础设施溯源分析"""

import asyncio
import logging
import re
import socket
import ssl
import subprocess
from typing import Optional
from urllib.parse import urlparse

import httpx

from apexcrawler.inspection_types import (
    DNSRecord,
    IPInfo,
    WhoIsInfo,
    SSLInfo,
    InfrastructureReport,
)

logger = logging.getLogger(__name__)


# ── 1. DNS 解析 ──


async def resolve_dns(domain: str) -> list[DNSRecord]:
    """解析域名的 DNS 记录（A/AAAA/NS/MX/TXT/CNAME）。

    优先使用系统命令 dig 获取完整记录，兜底使用 socket.getaddrinfo 获取 A/AAAA。
    """
    records: list[DNSRecord] = []

    # ── A / AAAA (通过 socket) ──
    for family, family_name in ((socket.AF_INET, "A"), (socket.AF_INET6, "AAAA")):
        try:
            infos = await asyncio.get_event_loop().getaddrinfo(domain, None, family=family, type=socket.SOCK_STREAM)
            seen = set()
            for info in infos:
                ip = info[4][0]
                if ip not in seen:
                    seen.add(ip)
                    records.append(DNSRecord(type=family_name, value=ip))
        except OSError:
            pass  # 没有 AAAA 或域名不可达

    async def _run_dig(qtype: str) -> None:
        """运行 dig +short 并解析结果"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "dig", "+short", qtype, domain,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            output = stdout.decode("utf-8", errors="replace").strip()
            if not output:
                return
            for line in output.splitlines():
                line = line.strip()
                if not line:
                    continue
                # 跳过 dig 的 CNAME 跟随结果（已包含 CNAME 本身）
                # NS 记录末尾可能带点，去掉
                records.append(DNSRecord(type=qtype.upper(), value=line.rstrip(".")))
        except (FileNotFoundError, asyncio.TimeoutError, subprocess.SubprocessError):
            pass

    # ── NS, MX, TXT, CNAME — 通过 dig ──
    await asyncio.gather(
        _run_dig("NS"),
        _run_dig("MX"),
        _run_dig("TXT"),
        _run_dig("CNAME"),
    )

    # ── 兜底: 如果 dig 没返回任何记录，尝试 nslookup ──
    if not any(r.type in ("NS", "MX", "TXT", "CNAME") for r in records):
        try:
            proc = await asyncio.create_subprocess_exec(
                "nslookup", "-type=any", domain,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            output = stdout.decode("utf-8", errors="replace")
            for line in output.splitlines():
                line = line.strip()
                # mail exchanger = ...
                m = re.search(r"mail exchanger\s*=\s*(\S+)", line, re.IGNORECASE)
                if m:
                    records.append(DNSRecord(type="MX", value=m.group(1).rstrip(".")))
                # nameserver = ...
                m = re.search(r"nameserver\s*=\s*(\S+)", line, re.IGNORECASE)
                if m:
                    records.append(DNSRecord(type="NS", value=m.group(1).rstrip(".")))
                # text = "..."
                m = re.search(r'text\s*=\s*"(.+)"', line, re.IGNORECASE)
                if m:
                    records.append(DNSRecord(type="TXT", value=m.group(1)))
                # canonical name = ...
                m = re.search(r"canonical name\s*=\s*(\S+)", line, re.IGNORECASE)
                if m:
                    records.append(DNSRecord(type="CNAME", value=m.group(1).rstrip(".")))
        except (FileNotFoundError, asyncio.TimeoutError, subprocess.SubprocessError):
            pass

    return records


# ── 2. IP 信息查询 ──


async def lookup_ip_info(ip: str) -> Optional[IPInfo]:
    """通过 ip-api.com 查询 IP 的地理位置和 ISP 信息。"""
    url = f"https://ip-api.com/json/{ip}?fields=status,country,regionName,city,isp,org,as,query"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("status") != "success":
                return None
            # as 字段格式: "AS15169 Google LLC"
            as_raw = data.get("as", "") or ""
            asn = None
            as_name = None
            if as_raw:
                parts = as_raw.split(" ", 1)
                asn = parts[0]  # e.g. "AS15169"
                as_name = parts[1] if len(parts) > 1 else None
            return IPInfo(
                ip=data.get("query", ip),
                country=data.get("country"),
                region=data.get("regionName"),
                city=data.get("city"),
                isp=data.get("isp"),
                org=data.get("org"),
                asn=asn,
                as_name=as_name,
            )
    except (httpx.RequestError, httpx.TimeoutException, ValueError) as exc:
        logger.warning("IP info lookup failed for %s: %s", ip, exc)
        return None


# ── 3. WHOIS 查询 ──


async def lookup_whois(domain: str) -> Optional[WhoIsInfo]:
    """通过系统 whois 命令查询域名注册信息。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "whois", domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        output = stdout.decode("utf-8", errors="replace")
    except (FileNotFoundError, asyncio.TimeoutError, subprocess.SubprocessError) as exc:
        logger.warning("WHOIS lookup failed for %s: %s", domain, exc)
        return None

    info = WhoIsInfo()

    # 检测隐私保护
    privacy_keywords = (
        "REDACTED FOR PRIVACY",
        "DATA REDACTED",
        "GDPR",
        "REDACTED",
        "PRIVACY PROTECTION",
        "Whois Privacy",
        "Personal Data",
    )
    for kw in privacy_keywords:
        if kw.lower() in output.lower():
            info.privacy_enabled = True
            break

    # 提取字段
    patterns: dict[str, str] = {
        "registrar": r"(?:Registrar|Sponsoring Registrar):\s*(.+)",
        "creation_date": r"(?:Creation Date|Created Date|Created on):\s*(.+)",
        "expiry_date": r"(?:Registry Expiry Date|Expiry Date|Expiration Date|Expires on):\s*(.+)",
        "registrant_org": r"(?:Registrant Organization|OrgName|org-name):\s*(.+)",
        "registrant_country": r"(?:Registrant Country|Country|country):\s*(.+)",
    }

    for attr, pattern in patterns.items():
        m = re.search(pattern, output, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            setattr(info, attr, val)

    # 提取 Name Server 列表
    for m in re.finditer(r"Name Server:\s*(\S+)", output, re.IGNORECASE):
        ns = m.group(1).strip().rstrip(".")
        if ns and ns not in info.name_servers:
            info.name_servers.append(ns)

    # 如果 name_servers 为空，尝试其他格式
    if not info.name_servers:
        for m in re.finditer(r"(?:nserver|nameserver):\s*(\S+)", output, re.IGNORECASE):
            ns = m.group(1).strip().rstrip(".")
            if ns and ns not in info.name_servers:
                info.name_servers.append(ns)

    return info


# ── 4. SSL 证书信息 ──


async def check_ssl(hostname: str, port: int = 443) -> Optional[SSLInfo]:
    """建立 SSL 连接并读取证书信息。"""
    try:
        ctx = ssl.create_default_context()
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(hostname, port, ssl=ctx),
            timeout=10,
        )
        sock = writer.get_extra_info("ssl_object")
        if sock is None:
            writer.close()
            return None
        cert = sock.getpeercert()
        writer.close()
        if cert is None:
            return None

        def _get_cn(dn: tuple) -> Optional[str]:
            """从 DN 元组中提取 CN"""
            for part in dn:
                if part[0] == "commonName":
                    return part[1]
            return None

        issuer_parts = cert.get("issuer", [])
        subject_parts = cert.get("subject", [])

        issuer_str = ", ".join(f"{p[0]}={p[1]}" for part in issuer_parts for p in part) if issuer_parts else None
        subject_str = ", ".join(f"{p[0]}={p[1]}" for part in subject_parts for p in part) if subject_parts else None

        return SSLInfo(
            issuer=issuer_str,
            subject=subject_str,
            valid_from=cert.get("notBefore"),
            valid_until=cert.get("notAfter"),
            sni=hostname,
        )
    except (OSError, asyncio.TimeoutError, ssl.SSLError) as exc:
        logger.warning("SSL check failed for %s:%d: %s", hostname, port, exc)
        return None


# ── 5. HTTP 头分析 ──


def _detect_cdn(headers: dict) -> Optional[str]:
    """从响应头检测 CDN 服务商。"""
    server = (headers.get("server") or "").lower()
    cf_ray = headers.get("cf-ray")
    x_cache = (headers.get("x-cache") or "").lower()
    via = (headers.get("via") or "").lower()

    if "cloudflare" in server or cf_ray:
        return "Cloudflare"
    if "cloudfront" in server or "cloudfront" in via:
        return "AWS CloudFront"
    if "akamaighost" in server or "akamai" in via:
        return "Akamai"
    if "fastly" in server or "fastly" in via:
        return "Fastly"
    if "jiasuma" in via or "213802" in via:
        return "加速马"
    if "ks-cdn" in via or ("x-cache" in headers and ("hit" in x_cache or "miss" in x_cache)):
        return "Kingsoft CDN"
    # 通用: x-cache hit/miss 通常表示 CDN
    if "hit" in x_cache or "miss" in x_cache:
        return "Unknown CDN"

    return None


async def fetch_headers(url: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """发送 HEAD 请求获取响应头，检测 Server、Content-Type 和 CDN。"""
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.head(url)
            headers = dict(resp.headers.items())
            server = headers.get("server")
            content_type = headers.get("content-type")
            cdn = _detect_cdn(headers)
            return server, content_type, cdn
    except (httpx.RequestError, httpx.TimeoutException) as exc:
        logger.warning("HEAD request failed for %s: %s", url, exc)
        return None, None, None


# ── 6. 技术栈检测 ──


def detect_backend(headers: dict, html_sample: str) -> Optional[str]:
    """从响应头和 HTML 片段检测后端技术栈。"""
    reasons = []

    x_powered = headers.get("x-powered-by", "")
    server = (headers.get("server") or "").lower()
    set_cookie = headers.get("set-cookie", "")

    if "ASP.NET" in x_powered:
        reasons.append("ASP.NET")
    if "PHP" in x_powered:
        reasons.append("PHP")
    if "PHPSESSID" in set_cookie:
        reasons.append("PHP")
    if "nginx" in server:
        reasons.append(f"nginx ({server.split('/')[-1]})" if "/" in server else "nginx")
    if "openresty" in server:
        reasons.append("OpenResty")
    if "apache" in server:
        reasons.append("Apache")
    if "IIS" in server:
        reasons.append("IIS")
    if "cloudflare" in server:
        reasons.append("Cloudflare")

    if "X-Generator" in headers:
        gen = headers["X-Generator"]
        if "JieQiCMS" in gen or "杰奇" in gen:
            reasons.append("杰奇CMS")

    if not reasons:
        return None
    return " / ".join(sorted(set(reasons)))


def detect_cms(html_sample: str) -> Optional[str]:
    """从 HTML 片段检测 CMS／建站系统。"""
    if not html_sample:
        return None

    html_lower = html_sample.lower()

    checks = [
        ("BQGBook (笔趣阁)", "/css/bqg.css"),
        ("3Q 自定义", "/3qdu/style/"),
        ("WordPress", "/wp-content/"),
        ("WordPress", "/wp-includes/"),
        ("Discuz!", "discuz"),
        ("DedeCMS", "dedecms"),
        ("DedeCMS", "dede"),
        ("帝国CMS", "empirecms"),
        ("帝国CMS", "ecms"),
        ("Joomla", "joomla"),
        ("Drupal", "drupal"),
        ("ThinkPHP", "thinkphp"),
        ("Laravel", "laravel"),
        ("Z-Blog", "zb_users"),
        ("Typecho", "typecho"),
        ("Z-Blog", "zblog"),
    ]

    matched = set()
    for name, pattern in checks:
        if pattern in html_lower:
            matched.add(name)

    if not matched:
        return None
    return " / ".join(sorted(matched))


# ── 工具函数 ──


def _extract_domain(url: str) -> Optional[str]:
    """从 URL 中提取域名。"""
    parsed = urlparse(url)
    hostname = parsed.hostname
    if hostname:
        return hostname
    # 尝试直接作为域名
    if "." in url and not url.startswith("http"):
        return url.split("/")[0]
    return None


# ── 7. 主入口 ──


async def analyze_infrastructure(url: str) -> InfrastructureReport:
    """对给定 URL 进行完整的基础设施溯源分析。

    协调 DNS 解析、IP 信息查询、WHOIS、SSL 检查、HTTP 头分析。
    所有步骤相互独立，单个失败不影响整体。
    """
    domain = _extract_domain(url)
    if not domain:
        return InfrastructureReport(domain=url or "")

    report = InfrastructureReport(domain=domain)

    # ── 并行执行独立任务 ──
    async def _resolve():
        report.dns_records = await resolve_dns(domain)
        # 提取 IPv4 地址
        report.resolved_ips = [r.value for r in report.dns_records if r.type == "A"]

    async def _ip_info():
        if report.resolved_ips:
            report.ip_info = await lookup_ip_info(report.resolved_ips[0])

    async def _whois():
        report.whois = await lookup_whois(domain)

    async def _ssl():
        report.ssl = await check_ssl(domain)

    async def _headers():
        server, content_type, cdn = await fetch_headers(url if url.startswith("http") else f"https://{domain}")
        report.server_header = server
        report.content_type = content_type
        report.cdn = cdn

    # 第一轮：并行 DNS + WHOIS + SSL + HEADERS
    # IP 信息依赖 DNS 结果，所以单独执行
    await asyncio.gather(
        _resolve(),
        _whois(),
        _ssl(),
        _headers(),
    )

    # 第二轮：IP 信息（依赖 DNS 结果）
    await _ip_info()

    return report
