"""HTTP header generator with Sec-CH-UA and consistency checks."""

from .tls_router import TLSProfile


def generate_headers(profile: TLSProfile) -> dict[str, str]:
    headers = {
        "User-Agent": profile.ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": profile.accept_language,
        "Accept-Encoding": "gzip, deflate, br",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
    }
    if profile.sec_ch_ua:
        headers["Sec-CH-UA"] = profile.sec_ch_ua
        headers["Sec-CH-UA-Platform"] = profile.sec_ch_ua_platform
        headers["Sec-CH-UA-Mobile"] = "?0"
    return headers
