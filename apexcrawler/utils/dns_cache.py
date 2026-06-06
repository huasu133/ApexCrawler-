"""Built-in DNS cache — eliminates 50-200ms DNS resolution delay."""

import socket
import time
import logging
import traceback

logger = logging.getLogger(__name__)


class DNSCache:
    """Simple in-process DNS cache with TTL."""

    def __init__(self, ttl: int = 600):
        self._cache: dict[str, tuple[str, float]] = {}
        self._ttl = ttl

    def resolve(self, host: str) -> str:
        if host in self._cache:
            ip, ts = self._cache[host]
            if time.monotonic() - ts < self._ttl:
                return ip
        try:
            ip = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)[0][4][0]
            self._cache[host] = (ip, time.monotonic())
            return ip
        except socket.gaierror:
            logger.debug("DNS 解析失败: %s", host)
            return host
        except socket.timeout:
            logger.debug("DNS 超时: %s", host)
            return host
        except Exception:
            logger.error("DNS 解析异常: %s\n%s", host, traceback.format_exc())
            return host

    def invalidate(self, host: str):
        self._cache.pop(host, None)

    def stats(self) -> dict:
        return {
            "cached": len(self._cache),
            "ttl": self._ttl,
            "entries": {h: ip for h, (ip, _) in self._cache.items()},
        }


# Global singleton
dns_cache = DNSCache()
