from __future__ import annotations

import socket
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class PortAllocation:
    instance_id: int
    socks_port: int


def _port_available(port: int) -> bool:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def generate_port_allocations(base_port: int, count: int, max_port: int) -> list[PortAllocation]:
    if base_port > max_port:
        raise ValueError("base_port must be lower than or equal to tor_max_port")
    allocations: list[PortAllocation] = []
    port = base_port
    index = 0
    while len(allocations) < count and port <= max_port:
        socks_port = port
        if _port_available(socks_port):
            allocations.append(PortAllocation(instance_id=index, socks_port=socks_port))
            index += 1
        port += 1
    if len(allocations) < count:
        raise RuntimeError(
            "Unable to allocate requested number of Tor ports; consider adjusting TOR_PROXY_TOR_BASE_PORT/TOR_PROXY_TOR_MAX_PORT"
        )
    return allocations


def chunked(sequence: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    for start in range(0, len(sequence), size):
        yield sequence[start : start + size]


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


