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

http_balancer = None
tor_pool = None
shutdown_event = threading.Event()

def signal_handler(sig, frame):
    shutdown_event.set()
    if http_balancer:
        http_balancer.stop()
    if tor_pool:
        tor_pool.stop()
    sys.exit(0)

def main():
    global http_balancer, tor_pool
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    tor_processes = int(os.environ.get('TOR_PROCESSES', '50'))
    
    logger.info("Запуск Rotating Tor HTTP Proxy")
    logger.info(f"Количество Tor процессов: {tor_processes}")
    
    http_balancer = HTTPLoadBalancer(listen_port=8080)
    http_balancer.start()
    
    config_manager = ConfigManager()
    relay_manager = TorRelayManager()
    
    tor_pool = TorPoolManager(
        config_manager=config_manager,
        load_balancer=http_balancer,
        relay_manager=relay_manager
    )
    
    if not tor_pool.start(tor_processes, skip_top_nodes=200):
        logger.error("Не удалось запустить пул Tor процессов")
        sys.exit(1)
    
    logger.info("HTTP прокси доступен по адресу: http://localhost:8080")
    logger.info("Сервисы запущены. Нажмите Ctrl+C для остановки.")

    while not shutdown_event.is_set():
        time.sleep(1)

if __name__ == "__main__":
    main()
