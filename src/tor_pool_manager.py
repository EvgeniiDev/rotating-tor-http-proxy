import logging
import threading
import time
from tor_process import TorInstance

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
        working_nodes = []
        
        # Тестируем exit-ноды через временные процессы
        for i, node in enumerate(exit_nodes[:count * 2]):  # Тестируем больше, чем нужно
            test_port = 30000 + i
            test_instance = TorInstance(test_port, [node], self.config_builder)
            
            try:
                test_instance.create_config()
                test_instance.start()
                time.sleep(3)
                
                if test_instance.check_health():
                    proxy = test_instance.get_proxies()
                    if self.checker.test_node(proxy):
                        working_nodes.append(node)
                        logger.info(f"Node {node} passed tests")
                        
                test_instance.stop()
                
                if len(working_nodes) >= count:
                    break
                    
            except Exception as e:
                logger.warning(f"Error testing node {node}: {e}")
                test_instance.stop()
                continue

        if not working_nodes:
            logger.error("No working exit nodes found after testing")
            return False
            
        logger.info(f"Found {len(working_nodes)} working exit nodes")

        # Запускаем нужное количество процессов через runner
        ports = [9050 + i for i in range(min(count, len(working_nodes)))]
        exit_nodes_for_runner = [[node] for node in working_nodes[:len(ports)]]
        
        self.runner.start_many(ports, exit_nodes_for_runner)
        
        # Добавляем в балансировщик
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
