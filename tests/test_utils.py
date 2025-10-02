"""Tests for utility functions."""

import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils import PortAllocation, _port_available, chunked, ensure_directory, generate_port_allocations


def test_port_available():
    """Test that _port_available correctly identifies available ports."""
    # Find an available port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('localhost', 0))
        port = s.getsockname()[1]
    
    # This port should be available after the socket is closed
    assert _port_available(port)
    
    # Test with a definitely unavailable port (65536 is out of range)
    # The function should handle the exception and return False
    assert not _port_available(65536)


def test_generate_port_allocations():
    """Test generation of port allocations."""
    # Use a larger range to ensure we can allocate ports
    allocations = generate_port_allocations(10000, 5, 11000)
    assert len(allocations) == 5
    assert all(isinstance(a, PortAllocation) for a in allocations)
    assert allocations[0].instance_id == 0
    assert allocations[1].instance_id == 1
    # Check that ports are sequential (but not necessarily starting at 10000)
    assert allocations[1].socks_port == allocations[0].socks_port + 1


def test_generate_port_allocations_insufficient_ports():
    """Test generation fails when not enough ports are available."""
    with pytest.raises(RuntimeError, match="Unable to allocate requested number"):
        generate_port_allocations(10000, 100, 10005)


def test_generate_port_allocations_invalid_range():
    """Test generation fails with invalid port range."""
    with pytest.raises(ValueError, match="base_port must be lower"):
        generate_port_allocations(10005, 5, 10000)


@patch('src.utils._port_available')
def test_generate_port_allocations_skips_unavailable_ports(mock_port_available):
    """Test that unavailable ports are skipped."""
    # Mock port availability to simulate some ports being unavailable
    mock_port_available.side_effect = lambda port: port != 10001  # Port 10001 is unavailable
    
    allocations = generate_port_allocations(10000, 3, 10010)
    assert len(allocations) == 3
    # Should skip port 10001 and use 10000, 10002, 10003
    assert allocations[0].socks_port == 10000
    assert allocations[1].socks_port == 10002
    assert allocations[2].socks_port == 10003


def test_chunked():
    """Test the chunked function."""
    data = list(range(10))
    
    # Test normal chunking
    chunks = list(chunked(data, 3))
    assert len(chunks) == 4
    assert chunks[0] == [0, 1, 2]
    assert chunks[1] == [3, 4, 5]
    assert chunks[2] == [6, 7, 8]
    assert chunks[3] == [9]
    
    # Test chunk size larger than data
    chunks = list(chunked(data, 15))
    assert len(chunks) == 1
    assert chunks[0] == data
    
    # Test invalid chunk size
    with pytest.raises(ValueError, match="chunk size must be positive"):
        list(chunked(data, 0))


def test_ensure_directory(tmp_path):
    """Test that ensure_directory creates directories."""
    test_dir = tmp_path / "test_subdir"
    assert not test_dir.exists()
    
    result = ensure_directory(test_dir)
    assert test_dir.exists()
    assert test_dir.is_dir()
    assert result == test_dir
    
    # Test it works on existing directory
    result = ensure_directory(test_dir)
    assert test_dir.exists()
    assert result == test_dir