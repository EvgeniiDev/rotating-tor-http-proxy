#!/usr/bin/env python3
import os
import sys
import logging
import signal
import time
import threading
import argparse

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
    
    # Парсинг аргументов командной строки
    parser = argparse.ArgumentParser(description='Rotating Tor HTTP Proxy')
    parser.add_argument('--test-nodes', action='store_true', 
                       help='Test exit nodes before starting the proxy')
    parser.add_argument('--test-only', action='store_true',
                       help='Only test exit nodes without starting the proxy')
    parser.add_argument('--skip-node-testing', action='store_true',
                       help='Skip exit node testing during startup (faster startup)')
    args = parser.parse_args()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    tor_processes = int(os.environ.get('TOR_PROCESSES', '50'))
    
    logger.info("Запуск Rotating Tor HTTP Proxy")
    logger.info(f"Количество Tor процессов: {tor_processes}")
    
    config_manager = ConfigManager()
    relay_manager = TorRelayManager()
    
    # Тестирование узлов
    if args.test_nodes or args.test_only:
        logger.info("Testing exit nodes...")
        
        # Получаем список узлов
        relay_data = relay_manager.fetch_tor_relays()
        if relay_data:
            exit_nodes = relay_manager.extract_relay_ips(relay_data)
            if exit_nodes:
                # Берем первые 30 узлов для тестирования
                test_nodes = [node['ip'] for node in exit_nodes[:30]]
                logger.info(f"Testing {len(test_nodes)} exit nodes")
                
                # Создаем временный пул для тестирования
                temp_balancer = HTTPLoadBalancer(listen_port=8081)
                temp_pool = TorPoolManager(
                    config_manager=config_manager,
                    load_balancer=temp_balancer,
                    relay_manager=relay_manager
                )
                
                # Тестируем узлы
                working_nodes = temp_pool.test_exit_nodes(test_nodes)
                
                logger.info(f"Testing completed: {len(working_nodes)}/{len(test_nodes)} nodes passed")
                if working_nodes:
                    logger.info("Working nodes:")
                    for i, node in enumerate(working_nodes[:10], 1):  # Показываем первые 10
                        logger.info(f"  {i}. {node}")
                    if len(working_nodes) > 10:
                        logger.info(f"  ... and {len(working_nodes) - 10} more")
                
                if args.test_only:
                    logger.info("Test-only mode, exiting")
                    return
            else:
                logger.error("No exit nodes found")
                if args.test_only:
                    sys.exit(1)
        else:
            logger.error("Failed to fetch relay data")
            if args.test_only:
                sys.exit(1)
    
    http_balancer = HTTPLoadBalancer(listen_port=8080)
    http_balancer.start()
    
    tor_pool = TorPoolManager(
        config_manager=config_manager,
        load_balancer=http_balancer,
        relay_manager=relay_manager
    )
    
    # Определяем, нужно ли тестировать узлы
    test_nodes = not args.skip_node_testing
    
    if not tor_pool.start(tor_processes, test_nodes=test_nodes):
        logger.error("Не удалось запустить пул Tor процессов")
        sys.exit(1)
    
    logger.info("HTTP прокси доступен по адресу: http://localhost:8080")
    logger.info("Сервисы запущены. Нажмите Ctrl+C для остановки.")

    while not shutdown_event.is_set():
        time.sleep(1)

if __name__ == "__main__":
    main()
