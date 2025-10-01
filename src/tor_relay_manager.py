from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp

from config_manager import TorProxySettings
from logging_utils import get_logger

_ONIONOO_SUMMARY_URL = "https://onionoo.torproject.org/summary"  # nosec B105


@dataclass(frozen=True)
class RelayNode:
    fingerprint: str
    address: str
    bandwidth: int


class TorRelayManager:
    """Retrieve and manage Tor exit nodes from public directory authorities."""

    def __init__(self, settings: TorProxySettings, client: Optional[aiohttp.ClientSession] = None) -> None:
        self._settings = settings
        self._client = client or aiohttp.ClientSession()
        self._logger = get_logger("relay")

    async def fetch_exit_relays(self, limit: Optional[int] = None) -> List[RelayNode]:
        params = {"limit": limit} if limit is not None else None
        async with self._client.get(_ONIONOO_SUMMARY_URL, params=params) as response:
            response.raise_for_status()
            payload = await response.json()
            relays: List[RelayNode] = []
            for relay in payload.get("relays", []):
                if "Exit" not in relay.get("flags", []):
                    continue
                bandwidth = int(relay.get("observed_bandwidth", relay.get("bandwidth", 0)))
                for address in relay.get("addresses", relay.get("a", [])):
                    relays.append(
                        RelayNode(
                            fingerprint=relay.get("fingerprint", ""),
                            address=address,
                            bandwidth=bandwidth,
                        )
                    )
            relays.sort(key=lambda relay: relay.bandwidth, reverse=True)
            if limit is not None:
                return relays[:limit]
            return relays

    async def distribute_exit_nodes(self, instance_count: int) -> Dict[int, List[str]]:
        if instance_count <= 0:
            return {}
        nodes_per_instance = self._settings.exit_nodes_per_instance
        max_nodes = self._settings.exit_nodes_max
        total_needed = 0
        if nodes_per_instance > 0:
            total_needed = nodes_per_instance * instance_count
        elif max_nodes > 0:
            total_needed = max_nodes

        limit = total_needed if total_needed > 0 else None
        relays = await self.fetch_exit_relays(limit=limit)
        mapping: Dict[int, List[str]] = {index: [] for index in range(instance_count)}
        if not relays or nodes_per_instance == 0:
            return mapping
        cursor = 0
        available = len(relays)
        for instance_id in range(instance_count):
            selection: List[str] = []
            for _ in range(nodes_per_instance):
                address = relays[cursor % available].address
                selection.append(address)
                cursor += 1
            mapping[instance_id] = selection
        return mapping

    async def close(self) -> None:
        await self._client.close()
