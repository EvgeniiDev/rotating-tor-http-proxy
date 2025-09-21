import logging
import threading
from typing import List, Dict, Any
from proxy_load_balancer import ProxyBalancer, StatsReporter

logger = logging.getLogger(__name__)


class HTTPLoadBalancer:
    """
    Отвечает за HTTP прокси сервер с распределением нагрузки между Tor процессами.

    Логика:
    - Принимает HTTP запросы и перенаправляет их через доступные Tor прокси
    - Управляет списком активных прокси (добавление/удаление)
    - Использует round-robin алгоритм для равномерного распределения запросов
    - Мониторит состояние прокси и автоматически исключает неработающие
    """
    __slots__ = (
        'listen_port', 'proxy_balancer', 'proxy_monitor', '_lock',
        'config', 'proxy_ports'
    )

    def __init__(self, listen_port: int = 8080):
        self.listen_port = listen_port
        self.proxy_balancer: ProxyBalancer = None
        self.proxy_monitor: StatsReporter = None
        self._lock = threading.Lock()
        self.config = {
            "server": {
                "host": "0.0.0.0",
                "port": listen_port
            },
            "proxies": [],
            "load_balancing_algorithm": "round_robin",
            "health_check_interval": 25,
            "connection_timeout": 60,
            "max_retries": 3,
            "proxy_rest_duration": 30
        }
        self.proxy_ports: List[int] = []

    def add_proxy(self, port: int):
        with self._lock:
            if port in self.proxy_ports:
                return
            proxy_config = {"host": "127.0.0.1", "port": port}
            self.proxy_ports.append(port)
            self.config["proxies"].append(proxy_config)
            if self.proxy_balancer:
                config_copy = self.config.copy()
                self.proxy_balancer.update_proxies(config_copy)
                logger.info(f"Added SOCKS5 proxy on port {port}")

    def remove_proxy(self, port: int):
        with self._lock:
            if port not in self.proxy_ports:
                return
            self.proxy_ports.remove(port)
            self.config["proxies"] = [
                p for p in self.config["proxies"] if p["port"] != port]
            if self.proxy_balancer:
                config_copy = self.config.copy()
                self.proxy_balancer.update_proxies(config_copy)
                logger.info(f"Removed SOCKS5 proxy on port {port}")

    def start(self):
        if self.proxy_balancer:
            return
        with self._lock:
            config_copy = self.config.copy()
        
        logger.info(f"Creating HTTP Load Balancer with config: {config_copy}")
        
        try:
            self.proxy_balancer = ProxyBalancer(config_copy)
            logger.info("✅ ProxyBalancer created successfully")
            
            self.proxy_balancer.start()
            logger.info(f"✅ HTTP Load Balancer started on port {self.listen_port}")
            
            self.proxy_monitor = StatsReporter(self.proxy_balancer)
            self.proxy_monitor.start_monitoring()
            logger.info("✅ Stats monitoring started")
            
        except Exception as e:
            logger.error(f"❌ Failed to start HTTP Load Balancer: {e}")
            import traceback
            traceback.print_exc()
            
            # Cleanup on failure
            if self.proxy_monitor:
                try:
                    self.proxy_monitor.stop_monitoring()
                except:
                    pass
                self.proxy_monitor = None
            if self.proxy_balancer:
                try:
                    self.proxy_balancer.stop()
                except:
                    pass
                self.proxy_balancer = None
            raise

    def stop(self):
        logger.info("Stopping HTTP Load Balancer...")
        if self.proxy_monitor:
            self.proxy_monitor.stop_monitoring()
            self.proxy_monitor = None
        if self.proxy_balancer:
            self.proxy_balancer.stop()
            self.proxy_balancer = None
        with self._lock:
            self.proxy_ports.clear()
            self.config["proxies"].clear()
        logger.info("HTTP Load Balancer stopped successfully")

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'total_proxies': len(self.proxy_ports),
                'listen_port': self.listen_port,
                'proxy_ports': self.proxy_ports
            }

    def is_running(self) -> bool:
        return self.proxy_balancer is not None
