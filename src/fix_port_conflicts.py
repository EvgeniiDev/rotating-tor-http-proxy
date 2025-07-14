#!/usr/bin/env python3

import logging
import sys
import time

from proxy_manager import ProxyManager
from utils import is_port_available, ensure_port_available

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def check_and_fix_port_conflicts(num_proxies=5, base_tor_port=9050, base_http_port=8001, force_kill=False):
    """Check for port conflicts and optionally fix them."""
    
    conflicted_ports = []
    
    # Check Tor ports
    for i in range(num_proxies):
        tor_port = base_tor_port + i
        if not is_port_available('127.0.0.1', tor_port):
            conflicted_ports.append(('Tor', tor_port))
    
    # Check HTTP ports
    for i in range(num_proxies):
        http_port = base_http_port + i
        if not is_port_available('127.0.0.1', http_port):
            conflicted_ports.append(('HTTP', http_port))
    
    # Check HAProxy ports
    if not is_port_available('127.0.0.1', 8080):
        conflicted_ports.append(('HAProxy', 8080))
    if not is_port_available('127.0.0.1', 8404):
        conflicted_ports.append(('HAProxy Stats', 8404))
    
    if not conflicted_ports:
        logger.info("No port conflicts detected")
        return True
    
    logger.warning(f"Found {len(conflicted_ports)} port conflicts:")
    for service_type, port in conflicted_ports:
        logger.warning(f"  - {service_type}: port {port}")
    
    if not force_kill:
        logger.info("Use --force-kill to automatically terminate conflicting processes")
        return False
    
    logger.info("Attempting to resolve port conflicts...")
    resolved = 0
    
    for service_type, port in conflicted_ports:
        logger.info(f"Freeing {service_type} port {port}...")
        if ensure_port_available('127.0.0.1', port, force_kill=True):
            logger.info(f"Successfully freed port {port}")
            resolved += 1
        else:
            logger.error(f"Failed to free port {port}")
    
    logger.info(f"Resolved {resolved}/{len(conflicted_ports)} port conflicts")
    return resolved == len(conflicted_ports)


def restart_proxy_services():
    """Restart the proxy services with better error handling."""
    try:
        manager = ProxyManager(num_proxies=5)
        
        # Force cleanup ports first
        manager.force_cleanup_ports()
        
        # Wait a moment for ports to be fully released
        time.sleep(3)
        
        # Initialize the proxy manager
        logger.info("Initializing proxy manager...")
        manager.initialize()
        
        # Start all services
        logger.info("Starting all services...")
        manager.start_all_services()
        
        # Check final status
        status = manager.get_status()
        logger.info(f"Final status: {status['healthy_proxies']}/{status['total_proxies']} proxies healthy")
        
        if status['healthy_proxies'] < status['total_proxies']:
            logger.warning("Some proxies failed to start, running diagnostics...")
            manager.diagnose_issues()
            
            # Try to restart failed services
            manager.restart_failed_services()
            
            # Check status again
            final_status = manager.get_status()
            logger.info(f"After restart: {final_status['healthy_proxies']}/{final_status['total_proxies']} proxies healthy")
        
        return manager
        
    except Exception as e:
        logger.error(f"Failed to start proxy services: {e}")
        return None


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Fix port conflicts and restart proxy services')
    parser.add_argument('--force-kill', action='store_true', 
                       help='Automatically kill processes using required ports')
    parser.add_argument('--check-only', action='store_true',
                       help='Only check for conflicts, do not restart services')
    parser.add_argument('--num-proxies', type=int, default=5,
                       help='Number of proxy services to check/start')
    
    args = parser.parse_args()
    
    # Check and fix port conflicts
    success = check_and_fix_port_conflicts(
        num_proxies=args.num_proxies,
        force_kill=args.force_kill
    )
    
    if not success:
        logger.error("Port conflicts detected. Use --force-kill to resolve them.")
        sys.exit(1)
    
    if args.check_only:
        logger.info("Port check completed successfully")
        sys.exit(0)
    
    # Restart services
    manager = restart_proxy_services()
    
    if manager is None:
        logger.error("Failed to start proxy services")
        sys.exit(1)
    
    logger.info("Proxy services started successfully")
    
    # Keep the script running to maintain services
    try:
        logger.info("Services are running. Press Ctrl+C to stop.")
        while True:
            time.sleep(60)
            status = manager.get_status()
            logger.info(f"Health check: {status['healthy_proxies']}/{status['total_proxies']} proxies healthy")
            
            if status['healthy_proxies'] < status['total_proxies']:
                logger.warning("Some proxies are unhealthy, attempting restart...")
                manager.restart_failed_services()
                
    except KeyboardInterrupt:
        logger.info("Shutting down services...")
        manager.stop_all_services()
        manager.cleanup_temp_files()
        logger.info("Services stopped successfully")
