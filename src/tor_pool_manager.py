import logging
import threading

logger = logging.getLogger(__name__)

class TorBalancerManager:
    """
    Координирует работу всего пула Tor процессов с HTTP балансировщиком.
    
    Логика:
    - Тестирует exit-ноды через ExitNodeChecker
    - Запускает рабочие Tor процессы через TorParallelRunner  
    - Регистрирует прокси в HTTPLoadBalancer для распределения нагрузки
    - Мониторит состояние пула и обеспечивает автоматическое восстановление
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
            
        logger.info(f"Starting pool with {count} processes using {len(exit_nodes)} exit nodes...")
        
        # Test more nodes than needed to have backup options
        nodes_to_test = min(len(exit_nodes), count * 3)
        logger.info(f"Testing {nodes_to_test} exit nodes to find {count} working nodes...")
        
        working_nodes = self.checker.test_exit_nodes_parallel(
            exit_nodes[:nodes_to_test], count
        )
        
        logger.info("✅ Test pool automatically cleaned up after node verification")
        
        if not working_nodes:
            logger.error("No working exit nodes found after testing")
            return False
            
        if len(working_nodes) < count:
            logger.warning(f"Found only {len(working_nodes)} working nodes, requested {count}. Will use {len(working_nodes)} processes.")
            
        logger.info(f"Found {len(working_nodes)} working exit nodes")

        # Create exactly as many ports as we have working nodes
        actual_count = min(count, len(working_nodes))
        ports = [10000 + i for i in range(actual_count)]
        exit_nodes_for_runner = [[node] for node in working_nodes[:actual_count]]
        
        logger.info(f"Starting {actual_count} Tor processes on ports {ports[0]}-{ports[-1]}...")
        self.runner.start_many(ports, exit_nodes_for_runner)
        
        with self._lock:
            for port in ports:
                self.http_balancer.add_proxy(port)
            if not self.http_balancer.is_running():
                self.http_balancer.start()
                
        logger.info(f"Successfully started {len(ports)} Tor processes and added to balancer")
        return True

    def remove_failed(self):
        """
        Removes failed processes from the balancer without spawning replacements.
        For full redistribution with replacement spawning, use redistribute_with_replacements().
        """
        with self._lock:
            statuses = self.runner.get_statuses()
            failed_ports = [port for port, status in statuses.items() if status.get('failed_checks', 0) >= 3]
            if failed_ports:
                logger.info(f"Removing {len(failed_ports)} failed processes from balancer")
                for port in failed_ports:
                    self.http_balancer.remove_proxy(port)
                    
    def redistribute_with_replacements(self, exit_nodes: list):
        with self._lock:
            statuses = self.runner.get_statuses()
            failed_ports = [port for port, status in statuses.items() if status.get('failed_checks', 0) >= 3]
            
            if failed_ports and exit_nodes:
                logger.info(f"Redistributing {len(failed_ports)} failed processes with replacements")
                
                for port in failed_ports:
                    self.http_balancer.remove_proxy(port)
                
                working_nodes = self.checker.test_exit_nodes_parallel(
                    exit_nodes, len(failed_ports)
                )
                
                if working_nodes:
                    new_ports = [port + 1000 for port in failed_ports]
                    exit_nodes_for_runner = [[node] for node in working_nodes]
                    
                    self.runner.start_many(new_ports, exit_nodes_for_runner)
                    
                    for port in new_ports:
                        self.http_balancer.add_proxy(port)
                    
                    logger.info(f"Successfully redistributed {len(new_ports)} replacement processes")
                else:
                    logger.warning("No working replacement nodes found")

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
