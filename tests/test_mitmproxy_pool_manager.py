"""Tests for the mitmproxy pool manager."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config_manager import TorProxySettings
from src.mitmproxy_pool_manager import MitmproxyPoolManager


@pytest.fixture
def settings():
    return TorProxySettings()


@pytest.fixture
def manager(settings):
    return MitmproxyPoolManager(settings)


@pytest.mark.asyncio
async def test_mitmproxy_pool_manager_initialization(settings):
    """Test that MitmproxyPoolManager initializes correctly."""
    manager = MitmproxyPoolManager(settings)
    assert manager._settings == settings
    assert manager._master is None
    assert manager._task is None


@pytest.mark.asyncio
async def test_mitmproxy_pool_manager_start(manager):
    """Test starting the mitmproxy pool manager."""
    with patch('src.mitmproxy_pool_manager.options') as mock_options, \
         patch('src.mitmproxy_pool_manager.DumpMaster') as mock_master, \
         patch('src.mitmproxy_pool_manager.MitmproxyBalancerAddon') as mock_addon:
        
        # Setup mocks
        mock_opts = MagicMock()
        mock_options.Options.return_value = mock_opts
        
        mock_dump_master = MagicMock()
        mock_master.return_value = mock_dump_master
        
        # Mock the async task with proper methods
        mock_task = MagicMock()
        mock_task.done.return_value = False  # Task is not done
        mock_task.exception.return_value = None  # No exception
        
        with patch('asyncio.create_task', return_value=mock_task):
            with patch('asyncio.sleep'):  # Mock the sleep to avoid delays
                # Test start method
                servers = [9050, 9051, 9052]
                await manager.start(servers)
                
                # Verify calls
                mock_options.Options.assert_called_once_with(listen_host="127.0.0.1", listen_port=8080)
                mock_master.assert_called_once_with(mock_opts)
                mock_addon.assert_called_once_with(
                    ['socks5://127.0.0.1:9050', 'socks5://127.0.0.1:9051', 'socks5://127.0.0.1:9052'],
                    10, 2, 30.0
                )
                
                # Verify the task was created
                assert manager._task is not None
                assert manager._master == mock_dump_master


@pytest.mark.asyncio
async def test_mitmproxy_pool_manager_start_exception_handling(manager):
    """Test exception handling during mitmproxy start."""
    with patch('src.mitmproxy_pool_manager.options'), \
         patch('src.mitmproxy_pool_manager.DumpMaster') as mock_master, \
         patch('src.mitmproxy_pool_manager.MitmproxyBalancerAddon'):
        
        # Setup mock to simulate immediate completion with exception
        mock_dump_master_instance = MagicMock()
        mock_master.return_value = mock_dump_master_instance
        
        # Create a task that's done with an exception
        mock_task = asyncio.Future()
        mock_task.set_exception(RuntimeError("Test exception"))
        
        with patch('asyncio.create_task', return_value=mock_task), \
             pytest.raises(RuntimeError, match="Failed to start mitmproxy master"):
            await manager.start([9050])


@pytest.mark.asyncio
async def test_mitmproxy_pool_manager_stop_with_active_master(manager):
    """Test stopping the mitmproxy pool manager with an active master."""
    with patch('src.mitmproxy_pool_manager.options') as mock_options, \
         patch('src.mitmproxy_pool_manager.DumpMaster') as mock_master, \
         patch('src.mitmproxy_pool_manager.MitmproxyBalancerAddon') as mock_addon:
        
        # Setup mocks
        mock_opts = MagicMock()
        mock_options.Options.return_value = mock_opts
        
        mock_dump_master = MagicMock()
        mock_master.return_value = mock_dump_master
        
        # Create a real awaitable task using asyncio.Future
        mock_task = asyncio.Future()
        # But we still need to mock the methods that the code calls
        mock_task.done = MagicMock(return_value=False)
        mock_task.exception = MagicMock(return_value=None)
        
        with patch('asyncio.create_task', return_value=mock_task):
            with patch('asyncio.sleep'):  # Mock the sleep to avoid delays
                # Start the manager first
                await manager.start([9050])
                
                # Now test stop
                await manager.stop()
                
                # Verify shutdown was called
                mock_dump_master.shutdown.assert_called_once()
                assert manager._master is None
                assert manager._task is None


@pytest.mark.asyncio
async def test_mitmproxy_pool_manager_stop_with_cancelled_task(manager):
    """Test stopping handles cancelled tasks properly."""
    with patch('src.mitmproxy_pool_manager.options') as mock_options, \
         patch('src.mitmproxy_pool_manager.DumpMaster') as mock_master, \
         patch('src.mitmproxy_pool_manager.MitmproxyBalancerAddon') as mock_addon:
        
        # Setup mocks
        mock_opts = MagicMock()
        mock_options.Options.return_value = mock_opts
        
        mock_dump_master = MagicMock()
        mock_master.return_value = mock_dump_master
        
        # Create a real awaitable task using asyncio.Future
        mock_task = asyncio.Future()
        # But we still need to mock the methods that the code calls
        mock_task.done = MagicMock(return_value=False)
        mock_task.exception = MagicMock(return_value=None)
        
        with patch('asyncio.create_task', return_value=mock_task):
            with patch('asyncio.sleep'):  # Mock the sleep to avoid delays
                # Start the manager first
                await manager.start([9050])
                
                # Cancel the task manually
                manager._task.cancel()
                
                # Now test stop - should handle CancelledError gracefully
                await manager.stop()
                
                assert manager._master is None
                assert manager._task is None