from __future__ import annotations

import asyncio
from typing import Dict

from .config_manager import TorProxySettings
from .mitmproxy_pool_manager import MitmproxyPoolManager
from .logging_utils import get_logger
from .tor_parallel_runner import TorParallelRunner
from .tor_relay_manager import TorRelayManager


class TorProxyIntegrator:
    """Coordinate Tor process pool, mitmproxy configuration, and monitoring."""

    def __init__(self, settings: TorProxySettings) -> None:
        self._settings = settings
        self._logger = get_logger("integrator")
        self._runner = TorParallelRunner(settings)
        self._relay_manager = TorRelayManager(settings)
        self._mitm_manager = MitmproxyPoolManager(settings)
        self._stop_event = asyncio.Event()

    async def start_pool(self) -> None:
        self._logger.info(
            "Starting Tor pool with %s instances", self._settings.tor_instances
        )

        exit_node_map = await self._relay_manager.distribute_exit_nodes(
            self._settings.tor_instances
        )
        instances = await self._runner.start_many(exit_node_map)

        active_socks = [inst.socks_port for inst in instances if inst.is_running]
        await self._mitm_manager.start(active_socks)

        # Start the monitor loop as a background task
        asyncio.create_task(self._monitor_loop())

    async def _monitor_loop(self) -> None:
        interval = self._settings.health_interval_seconds
        while not self._stop_event.is_set():
            await asyncio.sleep(interval)
            self._logger.debug("Running health cycle")
            await self._runner.perform_health_checks()
            await self._runner.restart_failed_instances()

    async def refresh_exit_nodes(self) -> None:
        exit_node_map = await self._relay_manager.distribute_exit_nodes(
            self._settings.tor_instances
        )
        for instance in self._runner.iter_instances():
            nodes = exit_node_map.get(instance.instance_id, [])
            if nodes:
                instance.update_exit_nodes(nodes)

    def rotate_circuits(self) -> None:
        self._logger.info("Requesting NEWNYM rotation across all Tor instances")
        self._runner.rotate_all_circuits()

    async def stop_pool(self) -> None:
        self._logger.info("Stopping Tor pool")
        self._stop_event.set()
        self._runner.stop_all()
        await self._relay_manager.close()
        await self._mitm_manager.stop()

    def get_stats(self) -> Dict[str, object]:
        statuses = self._runner.get_statuses()
        return {
            "instances": [status.__dict__ for status in statuses],
            "frontend_port": self._settings.frontend_port,
            "proxy_port": 8080,  # mitmproxy HTTP port
        }