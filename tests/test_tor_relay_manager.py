from __future__ import annotations

import pytest

from src.config_manager import TorProxySettings
from src.tor_relay_manager import TorRelayManager


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status = 200

    async def raise_for_status(self) -> None:
        if self.status >= 400:
            raise ValueError("error")

    async def json(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


class DummyClient:
    def __init__(self, payload):
        self._payload = payload
        self.requests = []

    def get(self, url, params=None):  # noqa: D401
        self.requests.append((url, params))
        response = DummyResponse(self._payload)
        return response

    async def close(self) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@pytest.mark.asyncio
async def test_fetch_exit_relays_filters_and_sorts():
    payload = {
        "relays": [
            {
                "fingerprint": "A",
                "observed_bandwidth": 50,
                "flags": ["Exit"],
                "a": ["1.1.1.1"],
            },
            {
                "fingerprint": "B",
                "observed_bandwidth": 100,
                "flags": ["Exit"],
                "a": ["2.2.2.2"],
            },
            {
                "fingerprint": "C",
                "observed_bandwidth": 10,
                "flags": ["Guard"],
                "a": ["3.3.3.3"],
            },
        ]
    }
    settings = TorProxySettings()
    manager = TorRelayManager(settings, client=DummyClient(payload))
    relays = await manager.fetch_exit_relays()
    assert [relay.address for relay in relays] == ["2.2.2.2", "1.1.1.1"]


@pytest.mark.asyncio
async def test_distribute_exit_nodes_assigns_unique_sets():
    payload = {
        "relays": [
            {
                "fingerprint": "A",
                "observed_bandwidth": 100,
                "flags": ["Exit"],
                "a": ["1.1.1.1", "1.1.1.2"],
            },
            {
                "fingerprint": "B",
                "observed_bandwidth": 90,
                "flags": ["Exit"],
                "a": ["2.2.2.2"],
            },
            {
                "fingerprint": "C",
                "observed_bandwidth": 80,
                "flags": ["Exit"],
                "a": ["3.3.3.3"],
            },
        ]
    }
    settings = TorProxySettings(exit_nodes_per_instance=2)
    manager = TorRelayManager(settings, client=DummyClient(payload))
    mapping = await manager.distribute_exit_nodes(instance_count=2)
    assert len(mapping) == 2
    assert all(len(nodes) == 2 for nodes in mapping.values())
    assert mapping[0] != mapping[1]
