import logging
import threading
import time
from typing import List, Dict, Optional, Any
from proxy_load_balancer.balancer import ProxyBalancer
from proxy_load_balancer.monitor import ProxyMonitor
from statistics_manager import StatisticsManager

logger = logging.getLogger(__name__)


class HTTPLoadBalancer:
    def __init__(self, listen_port: int = 8080):
        self.listen_port = listen_port
        self.proxy_balancer: Optional[ProxyBalancer] = None
        self.proxy_monitor: Optional[ProxyMonitor] = None
        self.stats_manager = StatisticsManager()
        self.config = {
            "server": {
                "host": "0.0.0.0",
                "port": listen_port
            },
            "proxies": [],
            "load_balancing_algorithm": "round_robin",
            "health_check_interval": 30,
            "connection_timeout": 60,
            "max_retries": 3
        }
        self.proxy_ports: List[int] = []
        self._lock = threading.Lock()
        self.available_proxies: List[int] = []
        self.unavailable_proxies: List[int] = []

    def add_proxy(self, port: int):
        with self._lock:
            if port in self.proxy_ports:
                return

            proxy_config = {"host": "127.0.0.1", "port": port}
            
            self.proxy_ports.append(port)
            self.config["proxies"].append(proxy_config)
            self.stats_manager.add_proxy(port)
            
            if self._test_proxy_connection(port):
                self.available_proxies.append(port)
                logger.info(f"Added available SOCKS5 proxy on port {port}")
            else:
                self.unavailable_proxies.append(port)
                logger.warning(f"Added unavailable SOCKS5 proxy on port {port}")
            
            if self.proxy_balancer:
                self.proxy_balancer.update_proxies(self.config)

    def remove_proxy(self, port: int):
        with self._lock:
            if port not in self.proxy_ports:
                return

            self.proxy_ports.remove(port)
            self.config["proxies"] = [p for p in self.config["proxies"] if p["port"] != port]
            
            if port in self.available_proxies:
                self.available_proxies.remove(port)
            if port in self.unavailable_proxies:
                self.unavailable_proxies.remove(port)
                
            self.stats_manager.remove_proxy(port)
            
            if self.proxy_balancer:
                self.proxy_balancer.update_proxies(self.config)
                
            logger.info(f"Removed SOCKS5 proxy on port {port}")

    def _test_proxy_connection(self, port: int) -> bool:
        import requests
        
        test_urls = ['http://httpbin.org/ip', 'http://icanhazip.com']
        
        session = requests.Session()
        session.proxies = {
            'http': f'socks5://127.0.0.1:{port}',
            'https': f'socks5://127.0.0.1:{port}'
        }
        
        for url in test_urls:
            try:
                response = session.get(url, timeout=10)
                if response.status_code == 200:
                    session.close()
                    return True
            except Exception as e:
                logger.debug(f"Test connection to {url} via port {port} failed: {e}")
                continue
        
        session.close()
        return False

    def start(self):
        if self.proxy_balancer:
            logger.warning("Proxy balancer is already running")
            return

        try:
            # Создаем простой балансировщик без мониторинга пока
            self.proxy_balancer = ProxyBalancer(self.config)
            logger.info(f"HTTP Load Balancer created with config: {self.config}")
            
            # Запускаем балансировщик
            self.proxy_balancer.start()
            logger.info(f"HTTP Load Balancer started on port {self.listen_port}")
            
            # Мониторинг отключаем пока для диагностики
            # self.proxy_monitor = ProxyMonitor(self.proxy_balancer)
            # self.proxy_monitor.start_monitoring()
            
        except Exception as e:
            logger.error(f"Failed to start HTTP load balancer: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise

    def stop(self):
        logger.info("Stopping HTTP Load Balancer...")
        
        if self.proxy_monitor:
            try:
                self.proxy_monitor.stop_monitoring()
            except Exception as e:
                logger.error(f"Error stopping monitor: {e}")
            self.proxy_monitor = None
            
        if self.proxy_balancer:
            try:
                self.proxy_balancer.stop()
            except Exception as e:
                logger.error(f"Error stopping balancer: {e}")
            self.proxy_balancer = None
            
        logger.info("HTTP Load Balancer stopped successfully")

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                'total_proxies': len(self.proxy_ports),
                'available_proxies': len(self.available_proxies),
                'unavailable_proxies': len(self.unavailable_proxies),
                'available_proxy_ports': self.available_proxies.copy(),
                'unavailable_proxy_ports': self.unavailable_proxies.copy(),
                'listen_port': self.listen_port,
                'proxy_stats': self.stats_manager.get_all_stats()
            }

    def get_proxy_list(self) -> List[int]:
        return self.proxy_ports.copy()

    def mark_proxy_success(self, port: int):
        self.stats_manager.record_request(port, True, 200)

    def mark_proxy_unavailable(self, port: int):
        self.stats_manager.record_request(port, False, 0)
        
        with self._lock:
            if port in self.available_proxies:
                self.available_proxies.remove(port)
                if port not in self.unavailable_proxies:
                    self.unavailable_proxies.append(port)

    def get_next_proxy(self) -> Optional[int]:
        with self._lock:
            if not self.available_proxies:
                return None
            
            import random
            return random.choice(self.available_proxies)

    def get_proxy_session(self, port: int):
        import requests
        
        session = requests.Session()
        session.proxies = {
            'http': f'socks5://127.0.0.1:{port}',
            'https': f'socks5://127.0.0.1:{port}'
        }
        return session

    def is_running(self) -> bool:
        return self.proxy_balancer is not None

    @property
    def server_thread(self):
        proxy_balancer = self.proxy_balancer
        
        class MockThread:
            def __init__(self, balancer_ref):
                self.balancer_ref = balancer_ref
                
            def is_alive(self):
                return self.balancer_ref is not None
        
        if hasattr(self, '_mock_thread'):
            return self._mock_thread
        
        self._mock_thread = MockThread(proxy_balancer)
        return self._mock_thread
