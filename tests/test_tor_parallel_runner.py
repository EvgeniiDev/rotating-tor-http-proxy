"""Tests for the Tor parallel runner."""

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.config_manager import TorProxySettings
from src.exceptions import TorInstanceError
from src.tor_parallel_runner import InstanceStatus, TorParallelRunner
from src.tor_process import TorInstance, TorRuntimeMetadata
from src.utils import PortAllocation


@pytest.fixture
def settings():
    return TorProxySettings()


@pytest.fixture
def runner(settings):
    return TorParallelRunner(settings)


def test_tor_parallel_runner_initialization(settings):
    """Test that TorParallelRunner initializes correctly."""
    runner = TorParallelRunner(settings)
    assert runner._settings == settings
    assert runner._tor_binary == "tor"
    assert runner._instances == {}
    assert runner._last_health == {}
    assert runner._last_error == {}
    assert isinstance(runner._lock, type(threading.RLock()))


def test_build_instance(runner):
    """Test building a Tor instance."""
    allocation = PortAllocation(instance_id=1, socks_port=9050)
    exit_nodes = ["node1", "node2"]
    
    with patch('src.tor_parallel_runner.TorInstance') as mock_instance:
        mock_instance.return_value = MagicMock()
        
        instance = runner._build_instance(allocation, exit_nodes)
        
        # Verify TorInstance was called with correct parameters
        mock_instance.assert_called_once()
        args, kwargs = mock_instance.call_args
        assert kwargs['instance_id'] == 1
        assert kwargs['tor_binary'] == "tor"
        assert kwargs['exit_nodes'] == exit_nodes


@pytest.mark.asyncio
async def test_start_single(runner):
    """Test starting a single Tor instance."""
    allocation = PortAllocation(instance_id=1, socks_port=9050)
    exit_nodes = ["node1", "node2"]
    
    # Mock the build_instance method
    mock_instance = MagicMock()
    mock_instance.instance_id = 1
    with patch.object(runner, '_build_instance', return_value=mock_instance):
        with patch.object(runner, '_start_instance_with_retries') as mock_start_retries:
            # Test the method
            result = await runner._start_single(allocation, exit_nodes)
            
            # Verify calls
            runner._build_instance.assert_called_once_with(allocation, exit_nodes)
            mock_start_retries.assert_called_once_with(mock_instance)
            assert result == mock_instance
            # Verify instance was stored
            assert runner._instances[1] == mock_instance


@pytest.mark.asyncio
async def test_start_instance_with_retries_success(runner):
    """Test successful instance start with retries."""
    mock_instance = MagicMock()
    mock_instance.instance_id = 1
    
    # Mock successful start
    with patch.object(runner, '_settings') as mock_settings:
        mock_settings.tor_start_retries = 0
        mock_settings.tor_start_timeout_seconds = 30.0
        
        mock_instance.start.return_value = None
        mock_instance.wait_until_ready = AsyncMock()
        
        # Test the method
        await runner._start_instance_with_retries(mock_instance)
        
        # Verify calls
        mock_instance.start.assert_called_once()
        mock_instance.wait_until_ready.assert_called_once_with(timeout=30.0)


@pytest.mark.asyncio
async def test_start_instance_with_retries_failure(runner):
    """Test failed instance start with retries."""
    mock_instance = MagicMock()
    mock_instance.instance_id = 1
    
    # Create a mock settings object with the required attributes
    mock_settings = MagicMock()
    mock_settings.tor_start_retries = 2
    mock_settings.tor_start_timeout_seconds = 30.0
    mock_settings.tor_start_retry_delay_seconds = 1.0
    
    # Mock the settings property
    with patch.object(runner, '_settings', mock_settings):
        mock_instance.start.return_value = None
        mock_instance.wait_until_ready = AsyncMock(side_effect=TorInstanceError("Test error"))
        mock_instance.force_kill.return_value = None
        
        # Test the method - should raise exception after retries
        with pytest.raises(TorInstanceError):
            await runner._start_instance_with_retries(mock_instance)
        
        # Verify start was called multiple times
        assert mock_instance.start.call_count == 3  # Initial + 2 retries
        assert mock_instance.force_kill.call_count == 3
        assert mock_instance.wait_until_ready.call_count == 3


def test_stop_all(runner):
    """Test stopping all Tor instances."""
    # Create mock instances
    mock_instance_1 = MagicMock()
    mock_instance_1.instance_id = 1
    mock_instance_2 = MagicMock()
    mock_instance_2.instance_id = 2
    
    runner._instances = {1: mock_instance_1, 2: mock_instance_2}
    
    # Test the method
    runner.stop_all()
    
    # Verify stop was called on all instances
    mock_instance_1.stop.assert_called_once()
    mock_instance_2.stop.assert_called_once()
    # Verify instances dict is cleared
    assert runner._instances == {}


def test_get_statuses(runner):
    """Test getting instance statuses."""
    # Create mock instances
    mock_instance_1 = MagicMock()
    mock_instance_1.instance_id = 1
    mock_instance_1.socks_port = 9050
    mock_instance_1.pid_file = "/path/to/pid1"
    mock_instance_1.is_running = True
    
    mock_instance_2 = MagicMock()
    mock_instance_2.instance_id = 2
    mock_instance_2.socks_port = 9051
    mock_instance_2.pid_file = "/path/to/pid2"
    mock_instance_2.is_running = False
    
    runner._instances = {1: mock_instance_1, 2: mock_instance_2}
    runner._last_health = {1: 1234567890.0}
    runner._last_error = {2: "Test error"}
    
    # Test the method
    statuses = runner.get_statuses()
    
    # Verify results
    assert len(statuses) == 2
    status_1 = next(s for s in statuses if s.instance_id == 1)
    status_2 = next(s for s in statuses if s.instance_id == 2)
    
    assert isinstance(status_1, InstanceStatus)
    assert status_1.instance_id == 1
    assert status_1.socks_port == 9050
    assert status_1.pid_file == "/path/to/pid1"
    assert status_1.running is True
    assert status_1.last_health_timestamp == 1234567890.0
    assert status_1.last_error is None
    
    assert isinstance(status_2, InstanceStatus)
    assert status_2.instance_id == 2
    assert status_2.socks_port == 9051
    assert status_2.pid_file == "/path/to/pid2"
    assert status_2.running is False
    assert status_2.last_health_timestamp is None
    assert status_2.last_error == "Test error"


@pytest.mark.asyncio
async def test_perform_health_checks(runner):
    """Test performing health checks on instances."""
    # Create mock instances
    mock_instance_1 = MagicMock()
    mock_instance_1.instance_id = 1
    mock_instance_2 = MagicMock()
    mock_instance_2.instance_id = 2
    
    runner._instances = {1: mock_instance_1, 2: mock_instance_2}
    mock_instance_1.perform_health_check = AsyncMock()
    mock_instance_2.perform_health_check = AsyncMock()
    
    # Test the method
    await runner.perform_health_checks()
    
    # Verify health checks were performed
    mock_instance_1.perform_health_check.assert_called_once()
    mock_instance_2.perform_health_check.assert_called_once()


@pytest.mark.asyncio
async def test_restart_failed_instances(runner):
    """Test restarting failed instances."""
    # Create mock instances
    mock_instance_1 = MagicMock()
    mock_instance_1.instance_id = 1
    mock_instance_1.is_running = True  # This one is running, should be skipped
    
    mock_instance_2 = MagicMock()
    mock_instance_2.instance_id = 2
    mock_instance_2.is_running = False  # This one is not running, should be restarted
    
    runner._instances = {1: mock_instance_1, 2: mock_instance_2}
    
    with patch.object(runner, '_start_instance_with_retries') as mock_start_retries:
        # Test the method
        await runner.restart_failed_instances()
        
        # Verify only the failed instance was restarted
        mock_start_retries.assert_called_once_with(mock_instance_2)
        mock_instance_1.assert_not_called()


def test_rotate_all_circuits(runner):
    """Test rotating circuits for all instances."""
    # Create mock instances
    mock_instance_1 = MagicMock()
    mock_instance_1.instance_id = 1
    mock_instance_1.is_running = True
    
    mock_instance_2 = MagicMock()
    mock_instance_2.instance_id = 2
    mock_instance_2.is_running = False  # Should be skipped
    
    mock_instance_3 = MagicMock()
    mock_instance_3.instance_id = 3
    mock_instance_3.is_running = True
    
    runner._instances = {1: mock_instance_1, 2: mock_instance_2, 3: mock_instance_3}
    
    # Test the method
    runner.rotate_all_circuits()
    
    # Verify rotate_circuits was called only on running instances
    mock_instance_1.rotate_circuits.assert_called_once()
    mock_instance_2.rotate_circuits.assert_not_called()
    mock_instance_3.rotate_circuits.assert_called_once()


def test_iter_instances(runner):
    """Test iterating over instances."""
    # Create mock instances
    mock_instance_1 = MagicMock()
    mock_instance_2 = MagicMock()
    
    runner._instances = {1: mock_instance_1, 2: mock_instance_2}
    
    # Test the method
    instances = list(runner.iter_instances())
    
    # Verify results
    assert len(instances) == 2
    assert mock_instance_1 in instances
    assert mock_instance_2 in instances


def test_remove_instance(runner):
    """Test removing an instance."""
    # Create mock instances
    mock_instance_1 = MagicMock()
    mock_instance_1.instance_id = 1
    
    mock_instance_2 = MagicMock()
    mock_instance_2.instance_id = 2
    
    runner._instances = {1: mock_instance_1, 2: mock_instance_2}
    runner._last_health = {1: 1234567890.0, 2: 1234567891.0}
    runner._last_error = {1: "Error 1", 2: "Error 2"}
    
    # Test removing existing instance
    runner.remove_instance(1)
    
    # Verify instance was removed
    assert 1 not in runner._instances
    assert 2 in runner._instances
    assert 1 not in runner._last_health
    assert 1 not in runner._last_error
    mock_instance_1.stop.assert_called_once()
    
    # Test removing non-existing instance (should not raise error)
    runner.remove_instance(999)
    assert len(runner._instances) == 1