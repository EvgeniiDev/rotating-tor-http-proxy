import aiohttp
import aiohttp_socks
from typing import Any

from mitmproxy import http

async def make_socks5_request(flow: http.HTTPFlow, proxy_url: str) -> http.Response:
    connector = aiohttp_socks.ProxyConnector.from_url(proxy_url)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        kwargs: dict[str, Any] = {
            "method": flow.request.method,
            "url": str(flow.request.url),
            "headers": {k: v for k, v in flow.request.headers.items()},
        }
        if flow.request.urlencoded_form:
            kwargs["data"] = dict(flow.request.urlencoded_form)
        elif flow.request.content:
            kwargs["data"] = flow.request.content

        async with session.request(**kwargs) as resp:
            content = await resp.read()
            headers = {k: v for k, v in resp.headers.items()}
            return http.Response.make(
                resp.status,
                content,
                headers,
            )
