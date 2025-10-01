class TorProxyError(Exception):
    """Base exception for proxy failures."""


class TorInstanceError(TorProxyError):
    """Raised when a Tor process cannot be managed correctly."""


class TorHealthCheckError(TorProxyError):
    """Raised when a Tor instance fails health verification."""
