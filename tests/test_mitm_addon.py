"""Tests for the mitmproxy addon components."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.mitm_addon.mitmproxy_balancer import (
    MitmproxyBalancerAddon, ProxyEndpoint, ProxyPool)
from src.mitm_addon.proxy_utils import make_socks5_request


def test_proxy_endpoint_available():
    """Test ProxyEndpoint availability checking."""
    endpoint = ProxyEndpoint(url="socks5://127.0.0.1:9050")
    
    # Should be available by default
    assert endpoint.available(time.monotonic())
    
    # Set cooldown and test
    endpoint.cooldown_until = time.monotonic() + 10.0
    assert not endpoint.available(time.monotonic())
    
    # Should be available after cooldown
    endpoint.cooldown_until = time.monotonic() - 1.0
    assert endpoint.available(time.monotonic())


def test_proxy_endpoint_cooldown():
    """Test ProxyEndpoint cooldown functionality."""
    endpoint = ProxyEndpoint(url="socks5://127.0.0.1:9050")
    endpoint.failures = 3
    
    current_time = time.monotonic()
    endpoint.start_cooldown(5.0)
    
    assert endpoint.failures == 0
    assert endpoint.cooldown_until >= current_time + 5.0


def test_proxy_endpoint_reset():
    """Test ProxyEndpoint reset functionality."""
    endpoint = ProxyEndpoint(url="socks5://127.0.0.1:9050")
    endpoint.failures = 5
    endpoint.cooldown_until = time.monotonic() + 10.0
    
    endpoint.reset()
    
    assert endpoint.failures == 0
    assert endpoint.cooldown_until == 0.0


def test_proxy_pool_initialization():
    """Test ProxyPool initialization."""
    endpoints = [
        ProxyEndpoint(url="socks5://127.0.0.1:9050"),
        ProxyEndpoint(url="socks5://127.0.0.1:9051"),
    ]
    
    pool = ProxyPool(
        endpoints=endpoints,
        failure_threshold=2,
        cooldown_seconds=15.0
    )
    
    assert len(pool._items) == 2
    assert len(pool._index) == 2
    assert pool._cursor == 0
    assert pool.failure_threshold == 2
    assert pool.cooldown_seconds == 15.0


def test_proxy_pool_empty_initialization():
    """Test ProxyPool initialization with empty endpoints."""
    with pytest.raises(ValueError, match="proxy pool cannot be empty"):
        ProxyPool(
            endpoints=[],
            failure_threshold=2,
            cooldown_seconds=15.0
        )


def test_proxy_pool_next():
    """Test ProxyPool next endpoint selection."""
    endpoints = [
        ProxyEndpoint(url="socks5://127.0.0.1:9050"),
        ProxyEndpoint(url="socks5://127.0.0.1:9051"),
    ]
    
    pool = ProxyPool(
        endpoints=endpoints,
        failure_threshold=2,
        cooldown_seconds=15.0
    )
    
    # Test normal selection
    endpoint1 = pool.next()
    endpoint2 = pool.next()
    endpoint3 = pool.next()
    
    assert endpoint1 is not None
    assert endpoint2 is not None
    assert endpoint3 is not None
    # Should cycle through endpoints
    assert endpoint1 != endpoint2 or len(endpoints) == 1
    assert endpoint3 == endpoints[0]  # Should cycle back


def test_proxy_pool_next_with_exclusion():
    """Test ProxyPool next endpoint selection with exclusion."""
    endpoints = [
        ProxyEndpoint(url="socks5://127.0.0.1:9050"),
        ProxyEndpoint(url="socks5://127.0.0.1:9051"),
    ]
    
    pool = ProxyPool(
        endpoints=endpoints,
        failure_threshold=2,
        cooldown_seconds=15.0
    )
    
    # Test excluding first endpoint
    endpoint = pool.next(exclude="socks5://127.0.0.1:9050")
    
    assert endpoint is not None
    assert endpoint.url == "socks5://127.0.0.1:9051"


def test_proxy_pool_next_with_cooldown():
    """Test ProxyPool next endpoint selection with cooldown."""
    endpoints = [
        ProxyEndpoint(url="socks5://127.0.0.1:9050"),
        ProxyEndpoint(url="socks5://127.0.0.1:9051"),
    ]
    
    # Put first endpoint in cooldown
    endpoints[0].start_cooldown(10.0)
    
    pool = ProxyPool(
        endpoints=endpoints,
        failure_threshold=2,
        cooldown_seconds=15.0
    )
    
    # Should select the available endpoint
    endpoint = pool.next()
    
    assert endpoint is not None
    assert endpoint.url == "socks5://127.0.0.1:9051"


def test_proxy_pool_mark_success():
    """Test ProxyPool marking endpoint as successful."""
    endpoints = [
        ProxyEndpoint(url="socks5://127.0.0.1:9050"),
    ]
    
    # Set up endpoint with failures and cooldown
    endpoints[0].failures = 3
    endpoints[0].cooldown_until = time.monotonic() + 10.0
    
    pool = ProxyPool(
        endpoints=endpoints,
        failure_threshold=2,
        cooldown_seconds=15.0
    )
    
    pool.mark_success("socks5://127.0.0.1:9050")
    
    assert endpoints[0].failures == 0
    assert endpoints[0].cooldown_until == 0.0


def test_proxy_pool_mark_failure():
    """Test ProxyPool marking endpoint as failed."""
    endpoints = [
        ProxyEndpoint(url="socks5://127.0.0.1:9050"),
    ]
    
    pool = ProxyPool(
        endpoints=endpoints,
        failure_threshold=2,
        cooldown_seconds=15.0
    )
    
    # First failure
    pool.mark_failure("socks5://127.0.0.1:9050")
    assert endpoints[0].failures == 1
    assert endpoints[0].cooldown_until == 0.0  # Not in cooldown yet
    
    # Second failure - should trigger cooldown
    pool.mark_failure("socks5://127.0.0.1:9050")
    assert endpoints[0].failures == 0  # Reset after cooldown
    assert endpoints[0].cooldown_until > time.monotonic()


def test_proxy_pool_urls():
    """Test ProxyPool urls method."""
    endpoints = [
        ProxyEndpoint(url="socks5://127.0.0.1:9050"),
        ProxyEndpoint(url="socks5://127.0.0.1:9051"),
    ]
    
    pool = ProxyPool(
        endpoints=endpoints,
        failure_threshold=2,
        cooldown_seconds=15.0
    )
    
    urls = pool.urls()
    assert len(urls) == 2
    assert "socks5://127.0.0.1:9050" in urls
    assert "socks5://127.0.0.1:9051" in urls


def test_mitmproxy_balancer_addon_initialization():
    """Test MitmproxyBalancerAddon initialization."""
    proxies = [
        "socks5://127.0.0.1:9050",
        "socks5://127.0.0.1:9051",
    ]
    
    addon = MitmproxyBalancerAddon(
        proxies=proxies,
        retry_limit=5,
        failure_threshold=3,
        cooldown_seconds=10.0
    )
    
    assert addon.retry_limit == 5
    assert addon.failure_threshold == 3
    assert addon.cooldown_seconds == 10.0
    assert addon.pool is not None
    assert len(addon.pool.urls()) == 2


@patch('src.mitm_addon.proxy_utils.aiohttp_socks.ProxyConnector')
@patch('src.mitm_addon.proxy_utils.aiohttp.ClientSession')
@pytest.mark.asyncio
async def test_make_socks5_request(mock_client_session, mock_proxy_connector):
    """Test make_socks5_request function."""
    # Setup mocks
    mock_connector_instance = MagicMock()
    mock_proxy_connector.from_url.return_value = mock_connector_instance
    
    mock_session_instance = MagicMock()
    mock_client_session.return_value.__aenter__.return_value = mock_session_instance
    
    mock_response = MagicMock()
    mock_response.read = AsyncMock(return_value=b"test content")
    mock_response.headers = {"Content-Type": "text/plain"}
    mock_response.status = 200
    
    # Mock the async context manager for session.request
    mock_request_context = AsyncMock()
    mock_request_context.__aenter__.return_value = mock_response
    mock_session_instance.request.return_value = mock_request_context
    
    # Create mock flow
    mock_flow = MagicMock()
    mock_flow.request.method = "GET"
    mock_flow.request.url = "http://example.com"
    mock_flow.request.headers.items.return_value = [("User-Agent", "test")]
    mock_flow.request.urlencoded_form = None
    mock_flow.request.content = None
    
    # Test the function
    result = await make_socks5_request(mock_flow, "socks5://127.0.0.1:9050")
    
    # Verify calls
    mock_proxy_connector.from_url.assert_called_once_with("socks5://127.0.0.1:9050")
    mock_client_session.assert_called_once()
    
    # Verify result
    assert result is not None
    assert result.status_code == 200
    assert result.content == b"test content"