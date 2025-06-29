#!/usr/bin/env python3
import os
import sys
import logging
import signal
import time
import threading


from http_load_balancer import HTTPLoadBalancer
from tor_network_manager import TorNetworkManager
from config_manager import ConfigManager
from tor_relay_manager import TorRelayManager
from tor_process_manager import TorProcessManager
from tor_health_monitor import TorHealthMonitor

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Глобальные переменные для компонентов
http_balancer = None
tor_manager = None
shutdown_event = threading.Event()

def signal_handler(sig, frame):
    shutdown_event.set()
    try:
        if http_balancer:
            http_balancer.stop()
        if tor_manager:
            tor_manager.stop_services()
    except Exception as e:
        pass
    sys.exit(0)

def main():
    global http_balancer, tor_manager
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    tor_processes = int(os.environ.get('TOR_PROCESSES', '50'))
    
    logger.info("Запуск Rotating Tor HTTP Proxy с внешним балансировщиком")
    logger.info(f"Количество Tor процессов: {tor_processes}")
    
    try:
        logger.info("Создание HTTPLoadBalancer...")
        http_balancer = HTTPLoadBalancer(listen_port=8080)
        http_balancer.start()
        
        logger.info("Создание зависимостей...")
        config_manager = ConfigManager()
        relay_manager = TorRelayManager()
        process_manager = TorProcessManager(config_manager, http_balancer)
        
        logger.info("Создание TorNetworkManager...")
        tor_manager = TorNetworkManager(
            None, 
            http_balancer, 
            config_manager, 
            relay_manager, 
            process_manager, 
            None
        )
        
        logger.info("Создание TorHealthMonitor...")
        health_monitor = TorHealthMonitor(
            tor_manager._restart_tor_instance_by_port,
            get_available_exit_nodes_callback=tor_manager._get_available_exit_nodes_for_health_monitor
        )
        
        tor_manager.health_monitor = health_monitor
    
        
        logger.info("Запуск мониторинга Tor...")
        tor_manager.start_monitoring()
        
        logger.info("Запуск Tor сервисов...")
        tor_manager.start_services(tor_processes)
        
        logger.info("HTTP прокси доступен по адресу: http://localhost:8080")
        logger.info("Конфигурация передается в балансировщик как словарь Python")
        
        logger.info("Сервисы запущены. Нажмите Ctrl+C для остановки.")
        
        try:
            while not shutdown_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Получен KeyboardInterrupt. Завершение работы...")
        finally:
            shutdown_event.set()
            if http_balancer:
                http_balancer.stop()
            if tor_manager:
                tor_manager.stop_services()
                    
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
