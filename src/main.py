#!/usr/bin/env python3
import os
import sys
import logging
import signal
import time
import threading

from http_load_balancer import HTTPLoadBalancer
from tor_pool_manager import TorPoolManager
from config_manager import ConfigManager
from tor_relay_manager import TorRelayManager

logging.basicConfig(
    level=logging.INFO,
    format='%(name)s - %(levelname)s - %(message)s'
)

logging.getLogger('urllib3.connectionpool').setLevel(logging.WARNING)
logging.getLogger('urllib3').setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
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
    except Exception:
        pass

def main():    
    cleanup_temp_files()
    
    tor_processes = int(os.environ.get('TOR_PROCESSES', '50'))
    
    logger.info("Запуск Rotating Tor HTTP Proxy")
    logger.info(f"Количество Tor процессов: {tor_processes}")
    
    config_manager = ConfigManager()
    relay_manager = TorRelayManager()
    
    http_balancer = HTTPLoadBalancer(listen_port=8081)
    http_balancer.start()
    
    tor_pool = TorPoolManager(
        config_manager=config_manager,
        load_balancer=http_balancer,
        relay_manager=relay_manager
    )
    
    if not tor_pool.start(tor_processes, test_nodes=True):
        logger.error("Не удалось запустить пул Tor процессов")
        sys.exit(1)
    
    logger.info("HTTP прокси доступен по адресу: http://localhost:8081")
    logger.info("Сервисы запущены. Нажмите Ctrl+C для остановки.")

    def signal_handler(sig, frame):
        shutdown_event.set()
        if http_balancer:
            http_balancer.stop()
        if tor_pool:
            tor_pool.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while not shutdown_event.is_set():
        time.sleep(1)

if __name__ == "__main__":
    main()
