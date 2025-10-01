import pytest


import src.utils as utils
from src.utils import chunked, generate_port_allocations


def test_generate_port_allocations_creates_unique_pairs(monkeypatch):
    monkeypatch.setattr(utils, "_port_available", lambda port: True)
    allocations = generate_port_allocations(10_000, 3, 10_010)
    assert len(allocations) == 3
    assert allocations[0].socks_port == 10_000
    assert allocations[1].socks_port == 10_001
    ports = {alloc.socks_port for alloc in allocations}
    assert len(ports) == 3


def test_chunked_splits_sequence():
    items = [1, 2, 3, 4, 5]
    chunks = list(chunked(items, 2))
    assert chunks == [[1, 2], [3, 4], [5]]


def test_chunked_rejects_non_positive_size():
    with pytest.raises(ValueError):
        list(chunked([1, 2], 0))


def test_generate_port_allocations_skips_occupied_ports(monkeypatch):
    def fake_port_available(port: int) -> bool:
        return port not in {10_000, 10_001}

    monkeypatch.setattr(utils, "_port_available", fake_port_available)
    allocations = generate_port_allocations(10_000, 2, 10_010)
    assert allocations[0].socks_port == 10_002
    assert allocations[1].socks_port == 10_003
