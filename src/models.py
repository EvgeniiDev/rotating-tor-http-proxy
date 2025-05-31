#!/usr/bin/env python3
"""
Data models for consistent API responses between backend and frontend
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class SubnetData:
    """Subnet information model"""
    subnet: str
    count: int  # Number of available Tor relays in this subnet
    status: str  # 'active', 'available', 'blocked', 'starting', 'stopping'
    limit: int  # Maximum number of instances allowed for this subnet
    running_instances: int = 0  # Number of currently running instances
    last_updated: Optional[str] = None  # ISO format timestamp
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


@dataclass
class Stats:
    """System statistics model"""
    active_subnets: int = 0
    blocked_subnets: int = 0
    total_subnets: int = 0
    tor_instances: int = 0
    running_instances: int = 0
    last_update: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


@dataclass
class ServiceStatus:
    """Service status model"""
    services_started: bool = False
    total_instances: int = 0
    running_tor: int = 0
    running_socks: int = 0
    haproxy_running: bool = False
    failed_instances: Optional[List[str]] = None
    last_check: Optional[str] = None
    
    def __post_init__(self):
        if self.failed_instances is None:
            self.failed_instances = []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


@dataclass
class ApiResponse:
    """Standard API response model"""
    success: bool
    message: str = ""
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        result = {
            'success': self.success,
            'message': self.message
        }
        if self.data:
            result.update(self.data)
        if self.error:
            result['error'] = self.error
        return result


@dataclass
class SubnetRequest:
    """Request model for subnet operations"""
    subnet: str
    instances: int = 1
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SubnetRequest':
        """Create from dictionary"""
        return cls(
            subnet=data.get('subnet', ''),
            instances=data.get('instances', 1)
        )


@dataclass
class ProxyTestResult:
    """Proxy test result model"""
    success: bool
    ip: Optional[str] = None
    response_time: Optional[float] = None
    error: Optional[str] = None
    timestamp: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)


def get_current_timestamp() -> str:
    """Get current timestamp in ISO format"""
    return datetime.now().isoformat()


def create_success_response(message: str = "Success", data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create successful API response"""
    return ApiResponse(success=True, message=message, data=data).to_dict()


def create_error_response(message: str, error: Optional[str] = None) -> Dict[str, Any]:
    """Create error API response"""
    return ApiResponse(success=False, message=message, error=error).to_dict()
