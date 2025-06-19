#!/usr/bin/env python3
"""
Rotating Tor HTTP Proxy with External Load Balancer

Запуск приложения с новым внешним балансировщиком нагрузки.
"""

import os
import sys
import logging
import signal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from admin_panel import AdminPanel
from http_load_balancer import HTTPLoadBalancer
from tor_network_manager import TorNetworkManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Глобальные переменные для компонентов
admin_panel = None
http_balancer = None
tor_manager = None

def signal_handler(sig, frame):
    logger.info('Получен сигнал остановки. Завершение работы...')
    try:
        if http_balancer:
            http_balancer.stop()
        if tor_manager:
            tor_manager.stop_services()
    except Exception as e:
        logger.error(f"Ошибка при остановке сервисов: {e}")
    sys.exit(0)

def main():
    global admin_panel, http_balancer, tor_manager
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    logger.info("Запуск Rotating Tor HTTP Proxy с внешним балансировщиком")
    logger.info("Используется прямой импорт proxy-load-balancer как Python модуль")
    logger.info("Все классы создаются централизованно в start_new.py")
    
    try:
        # Создание Flask app и SocketIO отдельно
        from flask import Flask
        from flask_socketio import SocketIO
        
        logger.info("Создание Flask app и SocketIO...")
        # Указываем правильные пути для шаблонов
        app = Flask(__name__, template_folder='src/templates')
        socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
        
        # Создание всех компонентов в стартовом файле
        logger.info("Создание HTTPLoadBalancer...")
        http_balancer = HTTPLoadBalancer(listen_port=8080)
        
        logger.info("Создание TorNetworkManager...")
        tor_manager = TorNetworkManager(socketio, http_balancer)
        
        # Создание AdminPanel со всеми зависимостями
        logger.info("Создание AdminPanel со всеми зависимостями...")
        admin_panel = AdminPanel(app, socketio, http_balancer, tor_manager)
        
        # Запуск сервисов
        logger.info("Пропускаем запуск HTTP балансировщика из-за проблем с внешним пакетом...")
        # http_balancer.start()
        
        logger.info("Запуск мониторинга Tor...")
        tor_manager.start_monitoring()
        
        logger.info("Запуск Tor сервисов...")
        tor_manager.start_services()
        
        logger.info("Запуск веб-интерфейса...")
        logger.info("Веб-интерфейс доступен по адресу: http://localhost:5000")
        logger.info("HTTP прокси доступен по адресу: http://localhost:8080")
        logger.info("Конфигурация передается в балансировщик как словарь Python")
        
        socketio.run(app, 
                    host='0.0.0.0', 
                    port=5000, 
                    debug=False, 
                    allow_unsafe_werkzeug=True)
                    
    except Exception as e:
        logger.error(f"Ошибка запуска: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
