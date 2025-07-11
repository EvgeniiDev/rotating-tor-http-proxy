import logging
import threading
from typing import List, Dict, Any
from proxy_load_balancer import ProxyBalancer, StatsReporter
from tor_process import TorInstance

logger = logging.getLogger(__name__)


class HTTPLoadBalancer:
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
            "health_check_interval": 15,
            "connection_timeout": 60,
            "max_retries": 3
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
            self.config["proxies"] = [p for p in self.config["proxies"] if p["port"] != port]
             
            if self.proxy_balancer:
                config_copy = self.config.copy()
                self.proxy_balancer.update_proxies(config_copy)
                logger.info(f"Removed SOCKS5 proxy on port {port}")


    def start(self):
        if self.proxy_balancer:
            return

        with self._lock:
            config_copy = self.config.copy()
        
        self.proxy_balancer = ProxyBalancer(config_copy)
        logger.info(f"HTTP Load Balancer created with config: {config_copy}")
        
        self.proxy_balancer.start()
        logger.info(f"HTTP Load Balancer started on port {self.listen_port}")
    
        self.proxy_monitor = StatsReporter(self.proxy_balancer)
        self.proxy_monitor.start_monitoring()

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


class TorBalancerManager:
    """
    Получает пригодные exit-ноды, запускает заданное число Tor-процессов, распределяет exit-ноды и добавляет их в балансировщик.
    """
    def __init__(self, config_builder, checker, runner, balancer: HTTPLoadBalancer):
        self.config_builder = config_builder
        self.checker = checker
        self.runner = runner
        self.balancer = balancer
        self._lock = threading.RLock()

    def run_pool(self, count: int, exit_nodes: list):
        """
        Запускает пул Tor-процессов с проверенными exit-нодами и добавляет их в балансировщик.
        """
        if not exit_nodes:
            logger.warning("No exit nodes provided")
            return False

        # 1. Проверить exit-ноды через checker (создаём временные прокси для тестирования)
        logger.info(f"Testing {len(exit_nodes)} exit nodes...")
        test_proxies = []
        for i, node in enumerate(exit_nodes):
            # Создаём временный Tor-процесс для тестирования
            test_port = 30000 + i
            test_instance = TorInstance(test_port, [node], self.config_builder)
            test_instance.create_config()
            test_instance.start()
            
            # Ждём запуска и тестируем
            import time
            time.sleep(5)
            if test_instance.check_health():
                proxy = test_instance.get_proxies()
                if self.checker.test_node(proxy):
                    test_proxies.append(proxy)
            
            test_instance.stop()

        if not test_proxies:
            logger.error("No working exit nodes found after testing")
            return False

        logger.info(f"Found {len(test_proxies)} working exit nodes")

        # 2. Запустить runner с подходящими exit-нодами
        ports = [9050 + i for i in range(min(count, len(test_proxies)))]
        exit_nodes_for_runner = []
        for i, proxy in enumerate(test_proxies[:len(ports)]):
            # Извлекаем IP из прокси для runner
            proxy_url = proxy['http']
            ip = proxy_url.split('://')[1].split(':')[0]
            exit_nodes_for_runner.append([ip])

        self.runner.start_many(ports, exit_nodes_for_runner)

        # 3. Добавить их в балансировщик
        with self._lock:
            for port in ports:
                self.balancer.add_proxy(port)
            
            if not self.balancer.is_running():
                self.balancer.start()

        logger.info(f"Successfully started {len(ports)} Tor processes and added to balancer")
        return True

    def redistribute(self):
        """
        Перераспределяет exit-ноды между процессами.
        """
        with self._lock:
            statuses = self.runner.get_statuses()
            failed_ports = [port for port, status in statuses.items() 
                          if status.get('failed_checks', 0) >= 3]
            
            if failed_ports:
                logger.info(f"Redistributing {len(failed_ports)} failed processes")
                for port in failed_ports:
                    self.balancer.remove_proxy(port)
                    # Здесь можно добавить логику перезапуска с новыми exit-нодами

    def get_stats(self):
        """
        Получает статистику по пулу.
        """
        with self._lock:
            runner_stats = self.runner.get_statuses()
            balancer_stats = self.balancer.get_stats()
            
            return {
                'tor_processes': len(runner_stats),
                'running_processes': len([s for s in runner_stats.values() if s.get('is_running')]),
                'balancer': balancer_stats,
                'process_details': runner_stats
            }

    def stop(self):
        """
        Останавливает пул и балансировщик.
        """
        with self._lock:
            self.runner.stop_all()
            self.balancer.stop()
            logger.info("Tor pool and balancer stopped")
