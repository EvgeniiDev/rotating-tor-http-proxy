"""Mitmproxy addon implementing SOCKS5 proxy rotation with retry logic."""

import time
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence
from ..logging_utils import get_logger

from mitmproxy import http
from mitmproxy.http import Headers
from mitmproxy.connection import ConnectionState
from mitmproxy.net.server_spec import ServerSpec
from mitmproxy.proxy.mode_specs import server_spec

from .proxy_utils import make_socks5_request


@dataclass
class ProxyEndpoint:
    """Represents a single upstream proxy endpoint."""

    url: str
    failures: int = 0
    cooldown_until: float = 0.0
    spec: ServerSpec = field(init=False)

    def __post_init__(self) -> None:
        
        # хак для обхода валидации
        hacked_url = self.url.replace("socks5", "tcp")
        
        scheme, address = server_spec.parse(hacked_url, "http")
        self.spec = ServerSpec((scheme, address))

    def available(self, now: float) -> bool:
        return self.cooldown_until <= now

    def start_cooldown(self, cooldown: float) -> None:
        self.cooldown_until = time.monotonic() + cooldown
        self.failures = 0

    def reset(self) -> None:
        self.cooldown_until = 0.0
        self.failures = 0


class ProxyPool:
    """Round-robin pool with basic failure tracking and cooldown."""

    def __init__(
        self,
        endpoints: Sequence[ProxyEndpoint],
        failure_threshold: int,
        cooldown_seconds: float,
    ) -> None:
        if not endpoints:
            raise ValueError("proxy pool cannot be empty")
        self._items = endpoints
        self._index: Dict[str, ProxyEndpoint] = {endpoint.url: endpoint for endpoint in endpoints}
        self._cursor: int = 0
        self.failure_threshold = max(1, failure_threshold)
        self.cooldown_seconds = max(0.0, cooldown_seconds)

    def next(self, *, exclude: Optional[str] = None) -> Optional[ProxyEndpoint]:
        now = time.monotonic()
        length = len(self._items)
        for _ in range(length):
            endpoint = self._items[self._cursor]
            self._cursor = (self._cursor + 1) % length
            if exclude and endpoint.url == exclude:
                continue
            if endpoint.available(now):
                return endpoint
        return None

    def mark_success(self, url: str) -> None:
        endpoint = self._index.get(url)
        if not endpoint:
            return
        endpoint.reset()

    def mark_failure(self, url: str) -> None:
        endpoint = self._index.get(url)
        if not endpoint:
            return
        endpoint.failures += 1
        if endpoint.failures >= self.failure_threshold:
            endpoint.start_cooldown(self.cooldown_seconds)

    def urls(self) -> List[str]:
        return list(self._index)


class MitmproxyBalancerAddon:
    METADATA_PROXY_URL = "balancer_proxy_url"

    def __init__(
        self,
        proxies: List[str],
        retry_limit: int = 10,
        failure_threshold: int = 2,
        cooldown_seconds: float = 15.0,
    ) -> None:
        if len(proxies) == 0:
            raise ValueError("Proxy configuration is empty")
        
        self.retry_limit = retry_limit
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.pool: Optional[ProxyPool] = None
        self.logger = get_logger("main")
        self.pool = self._load_pool(proxies)
        self.logger.info(f"Loaded {len(self.pool.urls())} upstream proxies for balancer")

    # ------------------------------------------------------------------
    # mitmproxy lifecycle hooks
    # ------------------------------------------------------------------
    async def request(self, flow: http.HTTPFlow) -> None:
        self.logger.info(f"Start handling {flow.request.host}")
        await self._perform_request_with_retry(flow)
        return

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    
    def _load_pool(self, proxies : List[str]) -> ProxyPool:
        endpoints: List[ProxyEndpoint] = []
        
        for proxy_url in proxies:
            endpoints.append(ProxyEndpoint(url=proxy_url))
        
        return ProxyPool(
            endpoints,
            failure_threshold=self.failure_threshold,
            cooldown_seconds=self.cooldown_seconds,
        )

    def _ensure_proxy(self, flow: http.HTTPFlow) -> None:
        if flow.metadata.get(self.METADATA_PROXY_URL):
            return
        
        endpoint = self.pool.next()
        if not endpoint:
            flow.response = http.Response(
                503,
                Headers([("Content-Type", "text/plain")]),
                b"No upstream proxies available",
                None,
                None,
                None,
            )
            return
        flow.metadata[self.METADATA_PROXY_URL] = endpoint.url
        
        if not self._apply_upstream_proxy(flow, endpoint):
            flow.response = http.Response(
                503,
                Headers([("Content-Type", "text/plain")]),
                b"Failed to assign upstream proxy",
                None,
                None,
                None,
            )
            return

    async def _perform_request_with_retry(self, flow: http.HTTPFlow) -> None:
        attempts = 0
        current_url = flow.metadata.get(self.METADATA_PROXY_URL)
        last_response = flow.response
        self.logger.info(f"Should Retry {flow.request.method} {flow.request.pretty_url}")
        while attempts < self.retry_limit:
            endpoint = self.pool.next(exclude=current_url)
            current_url = endpoint.url
            
            if not endpoint:
                self.logger.warn("No available proxies for retry")
                break

            self.logger.info(f"Retrying {flow.request.method} {flow.request.pretty_url} via {endpoint.url} (attempt {attempts + 1}/{self.retry_limit})")

            try:
                resp = await make_socks5_request(flow, endpoint.url)

                self.logger.info(resp.status_code)

                if resp.status_code == 200:
                    flow.response = resp
                    self.pool.mark_success(endpoint.url)
                    self.logger.info(f"Retry successful with status {resp.status_code}")
                    return
                else:
                    last_response = resp
                    self.pool.mark_failure(endpoint.url)
                    attempts += 1

            except Exception as e:
                self.logger.error(f"Retry failed: {e}")
                self.pool.mark_failure(endpoint.url)
                attempts += 1

        if last_response:
            flow.response = last_response
        else:
            self.logger.warn("No valid response available after retries")
        self.logger.info(f"Retry limit reached, returning last response with status {flow.response.status_code if flow.response else 'unknown'}")


    def _apply_upstream_proxy(self, flow: http.HTTPFlow, endpoint: ProxyEndpoint) -> bool:
        """Ensure the current flow routes through the desired upstream proxy."""
        server_conn = flow.server_conn
        if server_conn is None:
            self.logger.warn("Flow has no server connection; cannot assign upstream proxy")
            return False
        if server_conn.via == endpoint.spec:
            return True
        if server_conn.connected:
            try:
                new_server = replace(
                    server_conn,
                    via=endpoint.spec,
                    state=ConnectionState.CLOSED,
                    timestamp_start=None,
                    timestamp_end=None,
                    timestamp_tcp_setup=None,
                    timestamp_tls_setup=None,
                    peername=None,
                    sockname=None,
                    certificate_list=(),
                    alpn=None,
                    cipher=None,
                    cipher_list=(),
                    tls_version=None,
                    error=None,
                )
            except TypeError as exc:
                self.logger.warn(
                    f"Unable to clone server connection for upstream switch: {exc}"
                )
                return False
            flow.server_conn = new_server
        else:
            server_conn.via = endpoint.spec
        return True
