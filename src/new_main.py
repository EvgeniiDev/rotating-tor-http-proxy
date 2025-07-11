#!/usr/bin/env python3

import os
import sys
import logging
import signal
import time
import threading

from tor_orchestrator import TorOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger('requests').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

orchestrator = None
shutdown_event = threading.Event()


def cleanup_temp_files():
    import glob
    import shutil
    
    data_dir = os.path.expanduser('~/tor-http-proxy/data')
    if not os.path.exists(data_dir):
        return
    
    try:
        temp_patterns = [
            os.path.join(data_dir, 'data_*'),
            os.path.join(data_dir, 'torrc.*'),
            '/tmp/tor_*'
        ]
        
        for pattern in temp_patterns:
            for path in glob.glob(pattern):
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.unlink(path)
                except Exception:
                    pass
                    
        logger.info("Cleaned up temporary files")
        
    except Exception as e:
        logger.warning(f"Error cleaning up temp files: {e}")


def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, shutting down...")
    shutdown_event.set()
    
    global orchestrator
    if orchestrator:
        orchestrator.stop_system()
    
    sys.exit(0)


def setup_signal_handlers():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    if hasattr(signal, 'SIGHUP'):
        signal.signal(signal.SIGHUP, signal_handler)


def print_system_info():
    tor_processes = int(os.environ.get('TOR_PROCESSES', '20'))
    listen_port = int(os.environ.get('LISTEN_PORT', '8081'))
    validate_nodes = os.environ.get('VALIDATE_NODES', 'true').lower() == 'true'
    
    logger.info("=" * 60)
    logger.info("Rotating Tor HTTP Proxy - Refactored Architecture")
    logger.info("=" * 60)
    logger.info(f"Tor processes: {tor_processes}")
    logger.info(f"Listen port: {listen_port}")
    logger.info(f"Node validation: {'enabled' if validate_nodes else 'disabled'}")
    logger.info(f"Max concurrent processes: 20")
    logger.info(f"IP monitoring interval: 5 seconds")
    logger.info("=" * 60)
    
    return tor_processes, listen_port, validate_nodes


def monitor_system_status(orchestrator: TorOrchestrator):
    logger.info("System monitoring started")
    
    last_status_time = 0
    status_interval = 300
    
    while not shutdown_event.is_set():
        current_time = time.time()
        
        if current_time - last_status_time >= status_interval:
            try:
                status = orchestrator.get_system_status()
                
                logger.info("=" * 50)
                logger.info("SYSTEM STATUS")
                logger.info("=" * 50)
                logger.info(f"System status: {status.get('system_status')}")
                logger.info(f"Active processes: {status.get('active_processes')}")
                logger.info(f"Load balancer processes: {status.get('load_balancer_processes')}")
                logger.info(f"Validated nodes: {status.get('validated_nodes')}")
                logger.info(f"Listen port: {status.get('listen_port')}")
                
                pool_stats = status.get('pool_manager', {})
                logger.info(f"Pool - Total: {pool_stats.get('total_processes')}, "
                           f"Running: {pool_stats.get('running_processes')}, "
                           f"Failed: {pool_stats.get('failed_processes')}")
                
                logger.info("=" * 50)
                
                last_status_time = current_time
                
            except Exception as e:
                logger.error(f"Error getting system status: {e}")
        
        shutdown_event.wait(30)
    
    logger.info("System monitoring stopped")


def main():
    global orchestrator
    
    try:
        cleanup_temp_files()
        
        tor_processes, listen_port, validate_nodes = print_system_info()
        
        setup_signal_handlers()
        
        orchestrator = TorOrchestrator(listen_port=listen_port)
        
        logger.info("Starting Tor proxy system...")
        
        if not orchestrator.start_system(tor_processes, validate_nodes=validate_nodes):
            logger.error("Failed to start Tor proxy system")
            sys.exit(1)
        
        logger.info("Tor proxy system started successfully!")
        logger.info(f"HTTP proxy available at: http://localhost:{listen_port}")
        logger.info("Press Ctrl+C to stop the system")
        
        monitor_thread = threading.Thread(
            target=monitor_system_status,
            args=(orchestrator,),
            name="SystemMonitor"
        )
        monitor_thread.daemon = True
        monitor_thread.start()
        
        while not shutdown_event.is_set():
            time.sleep(1)
        
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
        shutdown_event.set()
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
        
    finally:
        if orchestrator:
            orchestrator.stop_system()
        
        cleanup_temp_files()
        logger.info("Application terminated")


if __name__ == "__main__":
    main()