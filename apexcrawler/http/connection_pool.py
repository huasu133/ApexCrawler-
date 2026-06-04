"""Pure Python aiohttp proxy layer — mimics Chrome connection pool behavior.

Architecture::

    Playwright  →  localhost:8080 (aiohttp proxy)  →  target

Why a local proxy?

Chrome's internal connection pool behaviour (6 connections per origin,
HTTP/2 multiplexing, 30s keepalive) is hard to replicate via raw
requests.  A local proxy intercepts every browser-initiated request,
forwards it through a precisely configured ``aiohttp.ClientSession``,
and returns the response — making the network trace indistinguishable
from real Chrome.

Chrome pool params:
- HTTP/1.1: max 6 connections per origin
- HTTP/2: 1 connection, multiplexed
- Idle timeout: 30s
- Max requests per connection: 100
"""

from __future__ import annotations

import logging
from typing import Any

from aiohttp import ClientSession, TCPConnector, web

logger = logging.getLogger(__name__)

# ── Chrome-matching TCP connector parameters ──────────────────
CHROME_CONNECTION_PARAMS: dict[str, Any] = {
    "limit": 6,
    "limit_per_host": 6,
    "force_close": False,
    "enable_cleanup_closed": True,
    "ttl_dns_cache": 300,
    "keepalive_timeout": 30,
}


class StealthProxy:
    """Local proxy server between Playwright and target.

    Usage::

        proxy = StealthProxy(port=8080)
        await proxy.start()
        # Launch Playwright with --proxy-server=proxy.proxy_url
        # ... crawl ...
        await proxy.stop()

    The proxy creates an ``aiohttp.ClientSession`` with Chrome-matching
    TCP connection parameters so the upstream server sees a realistic
    connection pattern.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._session: ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    @property
    def proxy_url(self) -> str:
        """URL suitable for ``--proxy-server``."""
        return f"http://{self._host}:{self._port}"

    async def start(self) -> None:
        """Start the proxy server.

        The proxy is non-blocking — it runs in the background via
        ``AppRunner`` and ``TCPSite``.
        """
        connector = TCPConnector(**CHROME_CONNECTION_PARAMS)
        self._session = ClientSession(connector=connector)

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self._handle)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()
        # Capture the actual port assigned by the OS when port=0
        if self._site is not None and self._site._server is not None and self._site._server.sockets:
            self._port = self._site._server.sockets[0].getsockname()[1]
        logger.info("StealthProxy: %s", self.proxy_url)

    async def stop(self) -> None:
        """Gracefully stop the proxy and close the client session."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._session:
            await self._session.close()
            self._session = None
        logger.info("StealthProxy stopped")

    async def _handle(self, request: web.Request) -> web.StreamResponse:
        """Forward every request to the upstream server."""
        if self._session is None:
            return web.Response(status=503, text="Proxy not started")

        # Filter hop-by-hop headers before forwarding
        HOP_BY_HOP = {
            "connection", "transfer-encoding", "upgrade",
            "keep-alive", "proxy-authorization", "te",
        }
        try:
            body = await request.read() if request.can_read_body else None
            forwarded_headers = {
                k: v for k, v in request.headers.items()
                if k.lower() not in HOP_BY_HOP
            }
            async with self._session.request(
                method=request.method,
                url=str(request.url),
                headers=forwarded_headers,
                data=body,
                allow_redirects=True,
            ) as upstream:
                resp = web.StreamResponse(
                    status=upstream.status,
                    headers=dict(upstream.headers),
                )
                await resp.prepare(request)
                async for chunk in upstream.content.iter_chunked(8192):
                    await resp.write(chunk)
                await resp.write_eof()
                return resp
        except Exception:
            logger.exception("Proxy error forwarding %s %s", request.method, request.url)
            return web.Response(status=502, text="Bad Gateway")


class ConnectionReuseManager:
    """Per-origin proxy management for engine pool integration.

    Maintains a pool of ``StealthProxy`` instances keyed by origin,
    so that different origins get their own connection pools — just
    like Chrome's per-origin connection limits.

    Usage::

        manager = ConnectionReuseManager()
        url = await manager.get_proxy("https://example.com")
        # Launch Playwright with proxy=url
        # ...
        await manager.close_all()
    """

    def __init__(self) -> None:
        self._proxies: dict[str, StealthProxy] = {}
        self._next_port: int = 8080

    async def get_proxy(self, url: str) -> str:
        """Return a proxy URL suitable for the given URL's origin.

        If a proxy for this origin already exists it is reused;
        otherwise a new one is started with an incremented port.
        """
        from yarl import URL

        origin = f"{URL(url).scheme}://{URL(url).host}"
        if origin not in self._proxies:
            p = StealthProxy(port=self._next_port)
            await p.start()
            self._proxies[origin] = p
            self._next_port += 1
        return self._proxies[origin].proxy_url

    async def close_all(self) -> None:
        """Stop and close all managed proxies."""
        for origin, proxy in list(self._proxies.items()):
            await proxy.stop()
            logger.debug("Closed proxy for %s", origin)
        self._proxies.clear()
