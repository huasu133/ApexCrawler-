"""ApexCrawler 审查模块共享数据类型"""

from dataclasses import dataclass, field
from typing import Optional


# ── OSINT 层 ──

@dataclass
class DNSRecord:
    type: str  # A, AAAA, NS, MX, TXT, CNAME
    value: str


@dataclass
class IPInfo:
    ip: str
    country: Optional[str] = None
    region: Optional[str] = None
    city: Optional[str] = None
    isp: Optional[str] = None
    org: Optional[str] = None
    asn: Optional[str] = None
    as_name: Optional[str] = None
    reverse_dns: Optional[str] = None


@dataclass
class WhoIsInfo:
    registrar: Optional[str] = None
    creation_date: Optional[str] = None
    expiry_date: Optional[str] = None
    registrant_org: Optional[str] = None
    registrant_country: Optional[str] = None
    name_servers: list[str] = field(default_factory=list)
    privacy_enabled: bool = False


@dataclass
class SSLInfo:
    issuer: Optional[str] = None
    subject: Optional[str] = None
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    sni: Optional[str] = None


@dataclass
class InfrastructureReport:
    domain: str
    resolved_ips: list[str] = field(default_factory=list)
    dns_records: list[DNSRecord] = field(default_factory=list)
    whois: Optional[WhoIsInfo] = None
    ip_info: Optional[IPInfo] = None
    server_header: Optional[str] = None
    content_type: Optional[str] = None
    cdn: Optional[str] = None
    ssl: Optional[SSLInfo] = None
    detected_backend: Optional[str] = None
    detected_cms: Optional[str] = None


# ── Inspector 层 ──

@dataclass
class NetworkRequest:
    url: str
    method: str
    resource_type: str  # document, script, image, stylesheet, font, xhr, fetch, other
    status: int
    mime_type: Optional[str] = None
    content_length: int = 0
    is_third_party: bool = False
    category: str = "unknown"  # first_party, analytics, ad, cdn, seo, tracker, social, other_third_party


# 第三方域名分类规则
DOMAIN_CATEGORIES: dict[tuple[str, ...], str] = {
    # 分析统计
    ("hm.baidu.com", "pos.baidu.com", "cpro.baidu.com"): "analytics",
    ("cnzz.com", "s4.cnzz.com", "s5.cnzz.com", "s9.cnzz.com", "s11.cnzz.com", "s19.cnzz.com", "s22.cnzz.com"): "analytics",
    ("google-analytics.com", "googletagmanager.com", "doubleclick.net"): "analytics",
    # SEO 提交
    ("zhanzhang.toutiao.com", "lf1-cdn-tos.bytegoofy.com"): "seo",
    ("s.360.cn", "s.ssl.qhres2.com", "jspassport.ssl.qhimg.com"): "seo",
    # 社交
    ("connect.facebook.net", "connect.facebook.com", "platform.twitter.com"): "social",
    # CDN
    ("cdnjs.cloudflare.com", "cdn.jsdelivr.net", "unpkg.com", "cdn.staticfile.org", "sp0.baidu.com", "zz.bdstatic.com"): "cdn",
    # 广告联盟
    ("pagead2.googlesyndication.com", "googleads.g.doubleclick.net"): "ad",
    ("cpro.baidu.com", "cb.baidu.com"): "ad",
    ("wukanka.cc", "ujpzar.cn"): "ad",
    # Cloudflare
    ("cloudflare.com", "cloudflareinsights.com"): "cdn",
}


@dataclass
class ResourceAnalysis:
    """页面资源分析结果"""
    total_requests: int = 0
    first_party_count: int = 0
    third_party_count: int = 0
    requests: list[NetworkRequest] = field(default_factory=list)

    # 按类型汇总
    scripts: list[str] = field(default_factory=list)
    external_images: list[str] = field(default_factory=list)
    stylesheets: list[str] = field(default_factory=list)
    fonts: list[str] = field(default_factory=list)
    iframes: list[str] = field(default_factory=list)
    xhr_fetch: list[str] = field(default_factory=list)

    # 第三方域名分类
    third_party_domains: dict[str, list[str]] = field(default_factory=dict)  # domain -> categories

    # 可疑发现
    suspicious_domains: list[str] = field(default_factory=list)
    ad_like_domains: list[str] = field(default_factory=list)

    # 外联图片
    external_image_domains: dict[str, list[str]] = field(default_factory=dict)  # domain -> urls


@dataclass
class PageMetadata:
    title: Optional[str] = None
    content_length: int = 0
    charset: Optional[str] = None
    meta_tags: dict[str, str] = field(default_factory=dict)
    og_tags: dict[str, str] = field(default_factory=dict)


@dataclass
class InspectionReport:
    """完整的审查报告"""
    url: str
    timestamp: str
    page: PageMetadata = field(default_factory=PageMetadata)
    infrastructure: Optional[InfrastructureReport] = None
    resources: Optional[ResourceAnalysis] = None
    total_external_domains: int = 0
    error: Optional[str] = None
