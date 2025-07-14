import re
import socket


def is_valid_ipv4(ip: str) -> bool:
    """Check if the given string is a valid IPv4 address."""
    try:
        parts = ip.split('.')
        if len(parts) != 4:
            return False
        for part in parts:
            if not (0 <= int(part) <= 255):
                return False
        return True
    except (ValueError, AttributeError):
        return False


def is_port_available(host: str, port: int) -> bool:
    """Check if a port is available on the given host."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            return result != 0  # 0 means port is in use
    except socket.error:
        return False


def find_available_port(host: str, start_port: int, max_attempts: int = 100) -> int:
    """Find an available port starting from start_port."""
    for i in range(max_attempts):
        port = start_port + i
        if is_port_available(host, port):
            return port
    raise RuntimeError(f"Could not find available port starting from {start_port}")



