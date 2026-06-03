"""HTTP header generator with Sec-CH-UA and consistency checks."""

from .tls_router import TLSProfile


def generate_headers(profile: TLSProfile) -> dict[str, str]:
    return {
        "User-Agent": profile.ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": profile.accept_language,
        "Accept-Encoding": "gzip, deflate, br",
        "Sec-CH-UA": profile.sec_ch_ua or '"Google Chrome";v="124", "Chromium";v="124", "Not=A?Brand";v="24"',
        "Sec-CH-UA-Platform": profile.sec_ch_ua_platform or f'"{profile.platform}"',
        "Sec-CH-UA-Mobile": "?0",
        "Upgrade-Insecure-Requests": "1",
        "Cache-Control": "max-age=0",
        "Connection": "keep-alive",
    }
