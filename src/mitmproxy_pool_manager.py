from __future__ import annotations

from typing import Iterable

from .config_manager import TorProxySettings
from .logging_utils import get_logger
from mitmproxy import options
from mitmproxy.tools.dump import DumpMaster

import asyncio
from .mitm_addon.mitmproxy_balancer import MitmproxyBalancerAddon


class MitmproxyPoolManager:
    """Render and apply mitmproxy configuration for the Tor pool."""

    def __init__(self, settings: TorProxySettings) -> None:
        self._settings = settings
        self._logger = get_logger("mitmproxy")
        self._master: DumpMaster | None = None
        self._task: asyncio.Task | None = None

    async def start(self, servers: Iterable[int]) -> None:
        """Start the mitmproxy asynchronously with the given backend servers."""
        proxy_urls = [f"socks5://127.0.0.1:{port}" for port in servers]
        
        opts = options.Options(listen_host="127.0.0.1", listen_port=8080)
        self._master = DumpMaster(opts)
        self._master.addons.add(MitmproxyBalancerAddon(proxy_urls, 10, 2, 30.0))

        self._task = asyncio.create_task(self._master.run())

        await asyncio.sleep(1)  # Allow time for startup
        if self._task.done():
            if self._task.exception():
                raise RuntimeError(f"Failed to start mitmproxy master: {self._task.exception()}")
            else:
                raise RuntimeError("Mitmproxy master started but completed immediately")
        self._logger.info("Started mitmproxy master asynchronously")

    async def stop(self) -> None:
        """Stop the mitmproxy asynchronously."""
        if self._task:
            self._logger.info("Cancelling mitmproxy task")
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            
        if self._master:
            self._master.shutdown()
            self._master = None
