"""ApexCrawler Inspector — browser network request interception and analysis."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from apexcrawler.inspection_types import (
    DOMAIN_CATEGORIES,
    PageMetadata,
    InspectionReport,
    NetworkRequest,
    ResourceAnalysis,
)

logger = logging.getLogger(__name__)

# ── OSINT module: optional import ──

try:
    from apexcrawler.osint import analyze_infrastructure  # type: ignore[import-untyped]

    _HAS_OSINT = True
except ImportError:
    _HAS_OSINT = False
    logger.info("osint module not available, infrastructure analysis will be skipped")


# ── URL helpers ──


def _clean_url(url: str) -> str:
    """Ensure URL has a scheme and perform basic validation."""
    url = url.strip()
    if not url:
        raise ValueError("URL is empty")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+\-.]*://", url):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError(f"Invalid URL: {url}")
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported scheme: {parsed.scheme}")
    return url


def _extract_domain(url: str) -> str:
    """Extract hostname from a URL."""
    parsed = urlparse(url)
    return parsed.hostname or ""


def _main_domain(hostname: str) -> str:
    """Extract the main domain (strip www. prefix)."""
    return hostname[4:] if hostname.startswith("www.") else hostname


# ── Resource type classification ──


def _classify_resource_type(mime: str, url: str) -> str:
    """Classify resource type based on MIME type and URL extension."""
    mime_lower = (mime or "").lower()
    url_lower = url.lower()

    if mime_lower in ("text/html", "application/xhtml", "application/xhtml+xml"):
        return "document"
    if mime_lower in ("application/javascript", "text/javascript", "application/x-javascript"):
        return "script"
    if mime_lower == "text/css":
        return "stylesheet"
    if mime_lower.startswith("image/"):
        return "image"
    if mime_lower.startswith("font/") or mime_lower in (
        "application/font-woff",
        "application/font-woff2",
        "application/x-font-ttf",
        "application/x-font-otf",
    ):
        return "font"
    if mime_lower == "application/json":
        return "xhr"
    if mime_lower and mime_lower != "other":
        # Some common additional mappings
        if "font" in mime_lower:
            return "font"

    # Fallback: check URL extensions
    if re.search(r"\.js($|[?#])", url_lower):
        return "script"
    if re.search(r"\.css($|[?#])", url_lower):
        return "stylesheet"
    if re.search(r"\.(jpg|jpeg|png|gif|webp|svg|avif|ico)($|[?#])", url_lower):
        return "image"
    if re.search(r"\.(woff|woff2|ttf|otf|eot)($|[?#])", url_lower):
        return "font"
    if re.search(r"/api/|/graphql|/rest/", url_lower):
        return "xhr"

    return "other"


# ── Third-party domain detection ──


def _is_third_party(hostname: str, main_domain: str) -> bool:
    """Check if a hostname belongs to a third-party domain."""
    if not hostname or not main_domain:
        return False
    hostname = hostname.lower()
    md = main_domain.lower()
    # Exact match or subdomain of main domain → first party
    return not (hostname == md or hostname.endswith("." + md))


def _categorize_domain(hostname: str) -> str:
    """Categorize a third-party domain using DOMAIN_CATEGORIES and heuristics."""
    hostname_lower = hostname.lower()

    # Check DOMAIN_CATEGORIES
    for domains, category in DOMAIN_CATEGORIES.items():
        for d in domains:
            if hostname_lower == d or hostname_lower.endswith("." + d):
                return category

    # Heuristic checks for common ad/tracker domains
    # Check for ad/tracker keywords in the hostname
    ad_tracker_keywords = ["ad", "ads", "analytics", "track", "pixel"]
    for kw in ad_tracker_keywords:
        if kw in hostname_lower.split("."):
            return "tracker"
    # Broader substring checks
    if "analytics" in hostname_lower or "track" in hostname_lower:
        return "tracker"
    if re.search(r"(^|\.)ad[s]?[^a-z]|ads\.|\.ads\.", hostname_lower):
        return "tracker"
    if re.search(r"(^|\.)pixel[^a-z]|\.pixel\.", hostname_lower):
        return "tracker"

    # Ad-like image hosting / CDN patterns
    if "youku" in hostname_lower or re.search(r"(^|\.)pic[^a-z]|\.pic\.", hostname_lower):
        return "ad"

    return "other_third_party"


# ── Browser network interception ──


async def capture_requests(
    url: str,
    headless: bool = True,
    timeout: int = 30,
    proxy: Optional[str] = None,
) -> tuple[list[NetworkRequest], str]:
    """Launch CloakBrowser, intercept network requests, and return captured data.

    Returns:
        Tuple of (list of NetworkRequest, HTML content as string).
    """
    from cloakbrowser import launch_async

    requests_dict: dict[str, NetworkRequest] = {}
    html_content = ""
    browser = None
    main_domain = _main_domain(_extract_domain(url))

    async def _on_request(request):
        req_url = request.url
        if req_url in requests_dict:
            return
        parsed = urlparse(req_url)
        hostname = parsed.hostname or ""
        resource_type = request.resource_type or "other"
        requests_dict[req_url] = NetworkRequest(
            url=req_url,
            method=request.method,
            resource_type=resource_type,
            status=0,
        )

    async def _on_response(response):
        req_url = response.url
        if req_url in requests_dict:
            nr = requests_dict[req_url]
            nr.status = response.status
            content_type = response.headers.get("content-type", "")
            nr.mime_type = content_type.split(";")[0].strip() if content_type else None
            # Try to get content length from headers
            cl = response.headers.get("content-length")
            if cl:
                try:
                    nr.content_length = int(cl)
                except (ValueError, TypeError):
                    pass
            # Re-classify based on actual MIME type
            if nr.mime_type:
                classified = _classify_resource_type(nr.mime_type, req_url)
                if classified != "other":
                    nr.resource_type = classified
        else:
            # Response came without a captured request event (e.g., redirects)
            parsed = urlparse(req_url)
            hostname = parsed.hostname or ""
            content_type = response.headers.get("content-type", "")
            mime = content_type.split(";")[0].strip() if content_type else None
            requests_dict[req_url] = NetworkRequest(
                url=req_url,
                method="GET",
                resource_type=_classify_resource_type(mime or "", req_url),
                status=response.status,
                mime_type=mime,
                is_third_party=_is_third_party(parsed.hostname or "", main_domain),
                category=_categorize_domain(parsed.hostname or ""),
            )

    try:
        launch_kwargs: dict[str, object] = {"headless": headless}
        env_proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy")
        if proxy:
            launch_kwargs["proxy"] = {"server": proxy}
        elif env_proxy:
            launch_kwargs["proxy"] = {"server": env_proxy}
        browser = await launch_async(**launch_kwargs)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        async def _on_request_safe(req):
            try:
                await _on_request(req)
            except Exception:
                pass  # Non-fatal

        async def _on_response_safe(resp):
            try:
                await _on_response(resp)
            except Exception:
                pass  # Non-fatal

        page.on("request", lambda req: asyncio.create_task(_on_request_safe(req)))
        page.on("response", lambda resp: asyncio.create_task(_on_response_safe(resp)))

        try:
            await asyncio.wait_for(
                page.goto(url, wait_until="networkidle"),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Navigation timeout for %s, using captured data so far", url)

        # Extra wait for lazy-loaded resources
        await asyncio.sleep(5)

        # Try to get page content
        try:
            html_content = await page.content()
        except Exception:
            logger.warning("Failed to get page content", exc_info=True)

    except asyncio.TimeoutError:
        logger.warning("Overall timeout for %s", url)
    except Exception:
        logger.exception("Error during browser interception for %s", url)
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                logger.warning("Failed to close browser", exc_info=True)

    return list(requests_dict.values()), html_content


# ── HTML page content analysis ──


def analyze_page_content(
    html: str,
    main_domain: str,
) -> tuple[PageMetadata, list[str], list[str], list[str]]:
    """Analyze HTML content and extract metadata and external resources.

    Returns:
        Tuple of (PageMetadata, external script srcs, external iframe srcs, external img srcs).
    """
    metadata = PageMetadata()
    scripts: list[str] = []
    iframes: list[str] = []
    images: list[str] = []

    if not html:
        return metadata, scripts, iframes, images

    metadata.content_length = len(html.encode("utf-8"))

    # Charset
    m = re.search(r'<meta[^>]+charset\s*=\s*["\']?\s*([a-zA-Z0-9_\-]+)', html, re.IGNORECASE)
    if m:
        metadata.charset = m.group(1)

    # Title
    m = re.search(r'<title[^>]*>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
    if m:
        metadata.title = m.group(1).strip()

    # Meta tags (name/content)
    for m in re.finditer(
        r'<meta[^>]+name\s*=\s*["\']\s*([^"\']+)\s*["\'][^>]*content\s*=\s*["\']\s*([^"\']+)\s*["\']',
        html,
        re.IGNORECASE,
    ):
        metadata.meta_tags[m.group(1).strip()] = m.group(2).strip()
    # Also match reversed attribute order
    for m in re.finditer(
        r'<meta[^>]+content\s*=\s*["\']\s*([^"\']+)\s*["\'][^>]*name\s*=\s*["\']\s*([^"\']+)\s*["\']',
        html,
        re.IGNORECASE,
    ):
        metadata.meta_tags[m.group(2).strip()] = m.group(1).strip()

    # OG tags (property/content)
    for m in re.finditer(
        r'<meta[^>]+property\s*=\s*["\']\s*(og:[^"\']+)\s*["\'][^>]*content\s*=\s*["\']\s*([^"\']+)\s*["\']',
        html,
        re.IGNORECASE,
    ):
        metadata.og_tags[m.group(1).strip()] = m.group(2).strip()
    for m in re.finditer(
        r'<meta[^>]+content\s*=\s*["\']\s*([^"\']+)\s*["\'][^>]*property\s*=\s*["\']\s*(og:[^"\']+)\s*["\']',
        html,
        re.IGNORECASE,
    ):
        metadata.og_tags[m.group(2).strip()] = m.group(1).strip()

    # External scripts (excluding inline and main-domain)
    for m in re.finditer(
        r'<script[^>]+src\s*=\s*["\']\s*([^"\']+)\s*["\']',
        html,
        re.IGNORECASE,
    ):
        src = m.group(1).strip()
        if not src:
            continue
        parsed = urlparse(src)
        if not parsed.hostname:
            continue  # Relative path → first party
        if not _is_third_party(parsed.hostname, main_domain):
            continue  # First-party script
        scripts.append(src)

    # External iframes
    for m in re.finditer(
        r'<iframe[^>]+src\s*=\s*["\']\s*([^"\']+)\s*["\']',
        html,
        re.IGNORECASE,
    ):
        src = m.group(1).strip()
        if src:
            parsed = urlparse(src)
            # Only include iframes with a hostname (skip data:/javascript:)
            if parsed.hostname:
                iframes.append(src)

    # External images (exclude main-domain and data: URIs)
    for m in re.finditer(
        r'<img[^>]+src\s*=\s*["\']\s*([^"\']+)\s*["\']',
        html,
        re.IGNORECASE,
    ):
        src = m.group(1).strip()
        if not src or src.startswith("data:") or src.startswith("javascript:"):
            continue
        parsed = urlparse(src)
        if not parsed.hostname:
            continue  # Relative path → first party
        if not _is_third_party(parsed.hostname, main_domain):
            continue  # Skip first-party images
        images.append(src)

    return metadata, scripts, iframes, images


# ── Resource analysis report ──


def build_resource_analysis(
    requests: list[NetworkRequest],
    html: str,
    main_domain: str,
) -> ResourceAnalysis:
    """Build a ResourceAnalysis from intercepted requests and HTML analysis."""
    ra = ResourceAnalysis()
    ra.requests = requests
    ra.total_requests = len(requests)

    # Classify requests
    third_party_domain_map: dict[str, list[str]] = {}

    for nr in requests:
        parsed = urlparse(nr.url)
        hostname = parsed.hostname or ""
        if not hostname:
            continue

        is_third = _is_third_party(hostname, main_domain)
        nr.is_third_party = is_third

        if is_third:
            nr.category = _categorize_domain(hostname)
            ra.third_party_count += 1
            # Collect by domain
            if hostname not in third_party_domain_map:
                third_party_domain_map[hostname] = []
            if nr.category not in third_party_domain_map[hostname]:
                third_party_domain_map[hostname].append(nr.category)
        else:
            nr.category = "first_party"
            ra.first_party_count += 1

        # Collect by resource type
        if nr.resource_type == "script":
            if nr.url not in ra.scripts:
                ra.scripts.append(nr.url)
        elif nr.resource_type == "image":
            if nr.url not in ra.external_images:
                ra.external_images.append(nr.url)
        elif nr.resource_type == "stylesheet":
            if nr.url not in ra.stylesheets:
                ra.stylesheets.append(nr.url)
        elif nr.resource_type == "font":
            if nr.url not in ra.fonts:
                ra.fonts.append(nr.url)
        elif nr.resource_type in ("xhr", "fetch"):
            if nr.url not in ra.xhr_fetch:
                ra.xhr_fetch.append(nr.url)

    ra.third_party_domains = third_party_domain_map

    # HTML analysis for additional resources
    _, html_scripts, html_iframes, html_images = analyze_page_content(html, main_domain)
    for s in html_scripts:
        if s not in ra.scripts:
            ra.scripts.append(s)
    for fr in html_iframes:
        if fr not in ra.iframes:
            ra.iframes.append(fr)
    for img in html_images:
        if img not in ra.external_images:
            ra.external_images.append(img)
            # Track external image domains
            parsed = urlparse(img)
            hostname = parsed.hostname or ""
            if hostname:
                if hostname not in ra.external_image_domains:
                    ra.external_image_domains[hostname] = []
                if img not in ra.external_image_domains[hostname]:
                    ra.external_image_domains[hostname].append(img)

    # Detect suspicious and ad-like domains
    common_cdn_keywords = [
        "cdn", "cloudflare", "jsdelivr", "unpkg", "bootstrap",
        "jquery", "googleapis", "gstatic", "facebook", "twitter",
    ]
    for domain, categories in third_party_domain_map.items():
        cat_str = " ".join(categories)
        # Check if domain looks suspicious
        is_known_cdn = any(kw in domain for kw in common_cdn_keywords)

        if "tracker" in cat_str or "ad" in cat_str:
            ra.suspicious_domains.append(domain)
        elif not is_known_cdn and "other_third_party" in cat_str:
            # Unknown third-party domains are suspicious
            ra.suspicious_domains.append(domain)

        if "ad" in cat_str:
            if domain not in ra.ad_like_domains:
                ra.ad_like_domains.append(domain)

    # Also check external image domains for ad-like patterns
    for domain in ra.external_image_domains:
        cat = _categorize_domain(domain)
        if cat == "ad" and domain not in ra.ad_like_domains:
            ra.ad_like_domains.append(domain)

    return ra


# ── Main inspection entry point ──


async def inspect_url(
    url: str,
    headless: bool = True,
    timeout: int = 30,
    proxy: Optional[str] = None,
) -> InspectionReport:
    """Complete URL inspection: browser capture + infrastructure analysis + resource analysis."""
    url = _clean_url(url)
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    main_domain_str = _main_domain(hostname)

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Parallel execution
    infrastructure_task = None
    if _HAS_OSINT:
        infrastructure_task = asyncio.create_task(analyze_infrastructure(url))

    browser_task = asyncio.create_task(capture_requests(url, headless, timeout, proxy))

    browser_result = await browser_task
    requests, html_content = browser_result

    infrastructure = None
    if infrastructure_task is not None:
        try:
            infrastructure = await infrastructure_task
        except Exception:
            logger.exception("Infrastructure analysis failed")

    # Analyze page content
    page_meta, _, _, _ = analyze_page_content(html_content, main_domain_str)

    # Build resource analysis
    resources = build_resource_analysis(requests, html_content, main_domain_str)

    # Build report
    report = InspectionReport(
        url=url,
        timestamp=timestamp,
        page=page_meta,
        infrastructure=infrastructure,
        resources=resources,
        total_external_domains=len(resources.third_party_domains),
    )

    return report


# ── Report formatting ──


def format_inspection_report(report: InspectionReport) -> str:
    """Generate a human-readable formatted inspection report."""
    lines: list[str] = []
    sep = "═" * 50
    lines.append(f"{sep}")
    lines.append("  ApexCrawler Inspection Report")
    lines.append(f"{sep}")
    lines.append(f"  Target: {report.url}")
    lines.append(f"  Time:   {report.timestamp}")
    lines.append("")

    # ── Infrastructure ──
    infra = report.infrastructure
    lines.append("── Infrastructure ──")
    if infra:
        ip_count = len(infra.resolved_ips) if infra.resolved_ips else 0
        lines.append(f"  DNS: {ip_count} records")
        if infra.ip_info:
            lines.append(f"  IP: {infra.ip_info.ip or 'N/A'} ({infra.ip_info.city or '?'}/{infra.ip_info.country or '?'})")
            lines.append(f"  ASN: {infra.ip_info.asn or 'N/A'} {infra.ip_info.org or ''}")
        else:
            lines.append("  IP: N/A")
            lines.append("  ASN: N/A")
        lines.append(f"  Server: {infra.server_header or 'Unknown'}")
        lines.append(f"  CDN: {infra.cdn or 'None detected'}")
        lines.append(f"  Backend: {infra.detected_backend or 'Unknown'}")
        lines.append(f"  CMS: {infra.detected_cms or 'Unknown'}")
    else:
        lines.append("  (Infrastructure analysis not available)")
    lines.append("")

    # ── Page Info ──
    page = report.page
    lines.append("── Page Info ──")
    lines.append(f"  Title: {page.title or 'N/A'}")
    lines.append(f"  Size: {page.content_length} bytes")
    lines.append(f"  Charset: {page.charset or 'N/A'}")
    lines.append("")

    # ── Network Requests ──
    res = report.resources
    if res:
        lines.append("── Network Requests ──")
        lines.append(f"  Total: {res.total_requests}")
        lines.append(f"  1st party: {res.first_party_count}")
        lines.append(f"  3rd party: {res.third_party_count} ({len(res.third_party_domains)} unique domains)")
        lines.append("")

        # ── Third-Party Domains ──
        if res.third_party_domains:
            lines.append("── Third-Party Domains ──")
            for domain, categories in sorted(res.third_party_domains.items()):
                cat_str = ", ".join(categories)
                count = sum(1 for r in res.requests if r.is_third_party and urlparse(r.url).hostname == domain)
                lines.append(f"  {domain:<30} {cat_str:<20} ({count} requests)")
            lines.append("")

        # ── External Resources ──
        lines.append("── External Resources ──")
        lines.append(f"  Scripts: {len(res.scripts)}")
        lines.append(f"  Images: {len(res.external_images)}")
        lines.append(f"  Stylesheets: {len(res.stylesheets)}")
        lines.append(f"  Fonts: {len(res.fonts)}")
        lines.append(f"  XHR/Fetch: {len(res.xhr_fetch)}")
        lines.append(f"  Iframes: {len(res.iframes)}")
        lines.append("")

        # ── Suspicious Findings ──
        if res.suspicious_domains or res.ad_like_domains:
            lines.append("── Suspicious Findings ──")
            if res.suspicious_domains:
                lines.append(f"  Suspicious domains: {', '.join(res.suspicious_domains)}")
            if res.ad_like_domains:
                lines.append(f"  Ad-like domains: {', '.join(res.ad_like_domains)}")
        else:
            lines.append("── Suspicious Findings ──")
            lines.append("  None detected")
    else:
        lines.append("── Network Requests ──")
        lines.append("  (No network data available)")

    lines.append("")
    lines.append(f"{sep}")

    return "\n".join(lines)
