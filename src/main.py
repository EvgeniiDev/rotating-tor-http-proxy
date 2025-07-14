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
        
        logger.info("Initializing proxy manager...")
        manager.initialize()
        
        logger.info("Starting all services...")
        manager.start_all_services()
        
        logger.info("Proxy system is running. Press Ctrl+C to stop.")
        logger.info("Load balancer available on http://127.0.0.1:8080")
        
        while manager.running:
            time.sleep(10)
            status = manager.get_status()
            logger.info(f"Status: {status['healthy_proxies']}/{status['total_proxies']} proxies healthy, HAProxy: {'OK' if status['haproxy_healthy'] else 'FAILED'}")
        
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
