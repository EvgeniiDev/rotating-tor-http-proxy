#!/usr/bin/env python3

import logging
import signal
import sys
import time
from proxy_manager import ProxyManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

manager = None

def signal_handler(signum, frame):
    logger.info("Received shutdown signal")
    if manager:
        manager.stop_all_services()
        manager.cleanup_temp_files()
    sys.exit(0)

def main():
    global manager
    manager = None
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        num_proxies = int(sys.argv[1]) if len(sys.argv) > 1 else 5
        logger.info(f"Starting proxy manager with {num_proxies} proxies")
        
        manager = ProxyManager(num_proxies=num_proxies)
        
        # Auto-fix port conflicts before initializing
        logger.info("Checking for port conflicts...")
        from utils import ensure_port_available
        
        # Clean up any conflicting processes
        manager.force_cleanup_ports()
        
        # Wait for ports to be fully released
        time.sleep(3)
        
        logger.info("Initializing proxy manager...")
        manager.initialize()
        
        logger.info("Starting all services...")
        manager.start_all_services()
        
        # Check if we need to retry any failed services
        status = manager.get_status()
        if status['healthy_proxies'] < status['total_proxies']:
            logger.warning("Some services failed to start, running diagnostics and retrying...")
            manager.diagnose_issues()
            manager.restart_failed_services()
            
            # Final status check
            final_status = manager.get_status()
            logger.info(f"Final status: {final_status['healthy_proxies']}/{final_status['total_proxies']} proxies healthy")
        
        logger.info("Proxy system is running. Press Ctrl+C to stop.")
        logger.info("Load balancer available on http://127.0.0.1:8080")
        logger.info("HAProxy admin interface available on http://127.0.0.1:8404/stats")
        
        # Initial diagnosis after startup
        time.sleep(5)
        manager.diagnose_issues()
        
        status_check_count = 0
        while manager.running:
            time.sleep(10)
            status = manager.get_status()
            logger.info(f"Status: {status['healthy_proxies']}/{status['total_proxies']} proxies healthy, HAProxy: {'OK' if status['haproxy_healthy'] else 'FAILED'}")
            
            # Detailed diagnosis every 5 status checks (50 seconds)
            status_check_count += 1
            if status_check_count % 5 == 0:
                manager.diagnose_issues()
                
                # Auto-restart failed services if more than half are unhealthy
                if status['healthy_proxies'] < status['total_proxies'] // 2:
                    logger.warning("More than half of services are unhealthy, attempting restart...")
                    manager.restart_failed_services()
        
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except Exception as e:
        logger.error(f"Error: {e}")
    finally:
        if manager:
            manager.stop_all_services()
            manager.cleanup_temp_files()

if __name__ == "__main__":
    main()
