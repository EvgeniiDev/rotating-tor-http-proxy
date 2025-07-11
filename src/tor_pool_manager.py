import logging
import threading
import time
from typing import Dict, List, Set, Generator, Optional
from datetime import datetime
from utils import safe_stop_thread

from tor_process import TorProcess
from exit_node_tester import ExitNodeTester
from parallel_worker_manager import ParallelWorkerManager

logger = logging.getLogger(__name__)


class TorBalancerManager:
    """
    Интеграционный класс: управляет тестированием exit-нод, запуском TorParallelRunner,
    добавлением портов в HTTPLoadBalancer и мониторингом состояния.
    """
    def __init__(self, config_builder, checker, runner, http_balancer):
        self.config_builder = config_builder
        self.checker = checker
        self.runner = runner
        self.http_balancer = http_balancer
        self._lock = threading.RLock()

    def run_pool(self, count: int, exit_nodes: list):
        if not exit_nodes:
            logger.warning("No exit nodes provided")
            return False
        logger.info(f"Testing {len(exit_nodes)} exit nodes...")
        test_proxies = []
        for i, node in enumerate(exit_nodes):
            test_port = 30000 + i
            test_instance = TorProcess(test_port, [node], self.config_builder)
            test_instance.create_config()
            test_instance.start_process()
            import time
            time.sleep(5)
            if test_instance.check_health():
                proxy = test_instance.get_proxies()
                if self.checker.test_node(proxy):
                    test_proxies.append(proxy)
            test_instance.stop_process()
        if not test_proxies:
            logger.error("No working exit nodes found after testing")
            return False
        logger.info(f"Found {len(test_proxies)} working exit nodes")
        ports = [9050 + i for i in range(min(count, len(test_proxies)))]
        exit_nodes_for_runner = []
        for i, proxy in enumerate(test_proxies[:len(ports)]):
            proxy_url = proxy['http']
            ip = proxy_url.split('://')[1].split(':')[0]
            exit_nodes_for_runner.append([ip])
        self.runner.create_instances_with_nodes(self.config_builder, {port: {'exit_nodes': [ip]} for port, ip in zip(ports, exit_nodes_for_runner)})
        with self._lock:
            for port in ports:
                self.http_balancer.add_proxy(port)
            if not self.http_balancer.is_running():
                self.http_balancer.start()
        logger.info(f"Successfully started {len(ports)} Tor processes and added to balancer")
        return True

    def redistribute(self):
        with self._lock:
            statuses = self.runner.get_statuses()
            failed_ports = [port for port, status in statuses.items() if status.get('failed_checks', 0) >= 3]
            if failed_ports:
                logger.info(f"Redistributing {len(failed_ports)} failed processes")
                for port in failed_ports:
                    self.http_balancer.remove_proxy(port)
                    # Здесь можно добавить логику перезапуска с новыми exit-нодами

    def get_stats(self):
        with self._lock:
            runner_stats = self.runner.get_statuses()
            balancer_stats = self.http_balancer.get_stats()
            return {
                'tor_processes': len(runner_stats),
                'running_processes': len([s for s in runner_stats.values() if s.get('is_running')]),
                'balancer': balancer_stats,
                'process_details': runner_stats
            }

    def stop(self):
        with self._lock:
            self.runner.stop_all()
            self.http_balancer.stop()
            logger.info("Tor pool and balancer stopped")
