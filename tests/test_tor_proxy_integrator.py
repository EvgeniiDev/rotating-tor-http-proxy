"""Tests for the Tor proxy integrator."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config_manager import TorProxySettings
from src.tor_proxy_integrator import TorProxyIntegrator


@pytest.fixture
def settings():
    return TorProxySettings()





def test_tor_proxy_integrator_initialization(settings):
    """Test that TorProxyIntegrator initializes correctly."""
    # Mock the dependencies to avoid creating real aiohttp clients
    with patch('src.tor_proxy_integrator.TorParallelRunner'), \
         patch('src.tor_proxy_integrator.TorRelayManager') as mock_relay_manager, \
         patch('src.tor_proxy_integrator.MitmproxyPoolManager'):
        # Create a mock client for the relay manager
        mock_client = AsyncMock()
        mock_relay_manager.return_value = MagicMock(_client=mock_client)
        
        integrator = TorProxyIntegrator(settings)
        assert integrator._settings == settings
        assert isinstance(integrator._stop_event, asyncio.Event)


@pytest.mark.asyncio
async def test_start_pool(settings):
    """Test starting the Tor proxy pool."""
    # Mock the dependencies to avoid creating real aiohttp clients
    with patch('src.tor_proxy_integrator.TorParallelRunner') as mock_runner_class, \
         patch('src.tor_proxy_integrator.TorRelayManager') as mock_relay_manager_class, \
         patch('src.tor_proxy_integrator.MitmproxyPoolManager') as mock_mitm_manager_class:
        # Create a mock client for the relay manager
        mock_client = AsyncMock()
        mock_relay_manager = MagicMock(_client=mock_client)
        mock_relay_manager_class.return_value = mock_relay_manager
        
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_mitm_manager = MagicMock()
        mock_mitm_manager_class.return_value = mock_mitm_manager
        
        integrator = TorProxyIntegrator(settings)
        
        # Set up return values
        mock_relay_manager.distribute_exit_nodes = AsyncMock(return_value={})
        mock_runner.start_many = AsyncMock(return_value=[])
        mock_mitm_manager.start = AsyncMock()
        
        # Test the method
        await integrator.start_pool()
        
        # Verify calls
        mock_relay_manager.distribute_exit_nodes.assert_called_once_with(
            integrator._settings.tor_instances
        )
        mock_runner.start_many.assert_called_once_with({})
        mock_mitm_manager.start.assert_called_once_with([])


@pytest.mark.asyncio
async def test_refresh_exit_nodes(settings):
    """Test refreshing exit nodes."""
    # Mock the dependencies to avoid creating real aiohttp clients
    with patch('src.tor_proxy_integrator.TorParallelRunner') as mock_runner_class, \
         patch('src.tor_proxy_integrator.TorRelayManager') as mock_relay_manager_class, \
         patch('src.tor_proxy_integrator.MitmproxyPoolManager'):
        # Create a mock client for the relay manager
        mock_client = AsyncMock()
        mock_relay_manager = MagicMock(_client=mock_client)
        mock_relay_manager_class.return_value = mock_relay_manager
        
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        integrator = TorProxyIntegrator(settings)
        
        # Set up return values
        exit_node_map = {0: ["node1", "node2"], 1: ["node3", "node4"]}
        mock_relay_manager.distribute_exit_nodes = AsyncMock(return_value=exit_node_map)
        
        # Create mock instances
        mock_instance_0 = MagicMock()
        mock_instance_1 = MagicMock()
        mock_instance_0.instance_id = 0
        mock_instance_1.instance_id = 1
        
        mock_runner.iter_instances.return_value = [mock_instance_0, mock_instance_1]
        
        # Test the method
        await integrator.refresh_exit_nodes()
        
        # Verify calls
        mock_relay_manager.distribute_exit_nodes.assert_called_once_with(
            integrator._settings.tor_instances
        )
        mock_instance_0.update_exit_nodes.assert_called_once_with(["node1", "node2"])
        mock_instance_1.update_exit_nodes.assert_called_once_with(["node3", "node4"])



def test_rotate_circuits(settings):
    """Test rotating circuits."""
    # Mock the dependencies to avoid creating real aiohttp clients
    with patch('src.tor_proxy_integrator.TorParallelRunner') as mock_runner_class, \
         patch('src.tor_proxy_integrator.TorRelayManager') as mock_relay_manager_class, \
         patch('src.tor_proxy_integrator.MitmproxyPoolManager'):
        # Create a mock client for the relay manager
        mock_client = AsyncMock()
        mock_relay_manager = MagicMock(_client=mock_client)
        mock_relay_manager_class.return_value = mock_relay_manager
        
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        integrator = TorProxyIntegrator(settings)
        
        # Test the method
        integrator.rotate_circuits()
        
        # Verify calls
        mock_runner.rotate_all_circuits.assert_called_once()


@pytest.mark.asyncio
async def test_stop_pool(settings):
    """Test stopping the Tor proxy pool."""
    # Mock the dependencies to avoid creating real aiohttp clients
    with patch('src.tor_proxy_integrator.TorParallelRunner') as mock_runner_class, \
         patch('src.tor_proxy_integrator.TorRelayManager') as mock_relay_manager_class, \
         patch('src.tor_proxy_integrator.MitmproxyPoolManager') as mock_mitm_manager_class:
        # Create a mock client for the relay manager
        mock_client = AsyncMock()
        mock_relay_manager = MagicMock(_client=mock_client)
        mock_relay_manager_class.return_value = mock_relay_manager
        
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        mock_mitm_manager = MagicMock()
        mock_mitm_manager_class.return_value = mock_mitm_manager
        
        integrator = TorProxyIntegrator(settings)
        
        # Set up async mocks
        mock_relay_manager.close = AsyncMock()
        mock_mitm_manager.stop = AsyncMock()
        
        # Test the method
        await integrator.stop_pool()
        
        # Verify the stop event was set
        assert integrator._stop_event.is_set()
        
        # Verify calls
        mock_runner.stop_all.assert_called_once()
        mock_relay_manager.close.assert_called_once()
        mock_mitm_manager.stop.assert_called_once()


def test_get_stats(settings):
    """Test getting statistics."""
    # Mock the dependencies to avoid creating real aiohttp clients
    with patch('src.tor_proxy_integrator.TorParallelRunner') as mock_runner_class, \
         patch('src.tor_proxy_integrator.TorRelayManager') as mock_relay_manager_class, \
         patch('src.tor_proxy_integrator.MitmproxyPoolManager'):
        # Create a mock client for the relay manager
        mock_client = AsyncMock()
        mock_relay_manager = MagicMock(_client=mock_client)
        mock_relay_manager_class.return_value = mock_relay_manager
        
        mock_runner = MagicMock()
        mock_runner_class.return_value = mock_runner
        
        integrator = TorProxyIntegrator(settings)
        
        # Mock the runner
        mock_status = MagicMock()
        mock_status.__dict__ = {"instance_id": 0, "socks_port": 9050}
        mock_runner.get_statuses.return_value = [mock_status]
        
        # Test the method
        stats = integrator.get_stats()
        
        # Verify results
        assert "instances" in stats
        assert len(stats["instances"]) == 1
        assert stats["instances"][0]["instance_id"] == 0
        assert stats["instances"][0]["socks_port"] == 9050
        assert stats["frontend_port"] == integrator._settings.frontend_port
        assert stats["proxy_port"] == 8080