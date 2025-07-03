import logging
import threading
import traceback
import requests
from typing import List, Dict, Any
from proxy_load_balancer import ProxyBalancer, StatsReporter

logger = logging.getLogger(__name__)


class HTTPLoadBalancer:
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
            "health_check_interval": 15,
            "connection_timeout": 60,
            "max_retries": 3
        }
        self.proxy_ports: List[int] = []


    def add_proxy(self, port: int):
        with self._lock:
            if port in self.proxy_ports:
                logger.debug(f"Proxy on port {port} already exists")
                return

            proxy_config = {"host": "127.0.0.1", "port": port}
            
            self.proxy_ports.append(port)
            self.config["proxies"].append(proxy_config)
            
            print(f"Config updated after adding proxy {port}: {self.config}")
            
            if self.proxy_balancer:
                config_copy = self.config.copy()
                self.proxy_balancer.update_proxies(config_copy)
                logger.info(f"Added SOCKS5 proxy on port {port}")
            else:
                logger.warning(f"Proxy balancer not started, proxy on port {port} will be added when balancer starts")

    def remove_proxy(self, port: int):
        with self._lock:
            if port not in self.proxy_ports:
                logger.debug(f"Proxy on port {port} not found")
                return

            self.proxy_ports.remove(port)
            self.config["proxies"] = [p for p in self.config["proxies"] if p["port"] != port]
            
            print(f"Config updated after removing proxy {port}: {self.config}")
            
            if self.proxy_balancer:
                config_copy = self.config.copy()
                self.proxy_balancer.update_proxies(config_copy)
                logger.info(f"Removed SOCKS5 proxy on port {port}")
            else:
                logger.warning(f"Proxy balancer not started, cannot remove proxy on port {port}")


    def start(self):
        if self.proxy_balancer:
            logger.warning("Proxy balancer is already running")
            return

        try:
            with self._lock:
                config_copy = self.config.copy()
            
            self.proxy_balancer = ProxyBalancer(config_copy)
            logger.info(f"HTTP Load Balancer created with config: {config_copy}")
            
            self.proxy_balancer.start()
            logger.info(f"HTTP Load Balancer started on port {self.listen_port}")
        
            self.proxy_monitor = StatsReporter(self.proxy_balancer)
            self.proxy_monitor.start_monitoring()
            
        except Exception as e:
            logger.error(f"Failed to start HTTP load balancer: {e}")
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
                logger.error(f"Error stopping proxy balancer: {e}")
            self.proxy_balancer = None
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
