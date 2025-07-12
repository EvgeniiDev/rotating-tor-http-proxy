import requests
import time
import logging
from typing import List
from tor_parallel_runner import TorParallelRunner

logger = logging.getLogger(__name__)

class ExitNodeChecker:
    """
    Отвечает за проверку пригодности exit-нод для работы с целевыми сайтами.
    
    Логика:
    - Тестирует exit-ноды параллельно через TorParallelRunner
    - Проверяет доступность целевого URL через каждую ноду
    - Возвращает список рабочих нод для использования в пуле
    """
    def __init__(self, test_url: str = "https://steamcommunity.com/market/search?appid=730", 
                 test_requests_count: int = 2, required_success_count: int = 1, 
                 timeout: int = 10, config_builder=None, max_workers: int = 10):
        self.test_url = test_url
        self.test_requests_count = test_requests_count
        self.required_success_count = required_success_count
        self.timeout = timeout
        self.config_builder = config_builder
        self.max_workers = max_workers

    def test_node(self, proxy: dict) -> bool:
        success_count = 0
        for _ in range(self.test_requests_count):
            try:
                response = requests.get(self.test_url, proxies=proxy, timeout=self.timeout)
                if response.status_code == 200:
                    success_count += 1
                    if success_count >= self.required_success_count:
                        return True
            except Exception:
                continue
        return success_count >= self.required_success_count

    def test_nodes(self, proxies: List[dict]) -> List[dict]:
        return [proxy for proxy in proxies if self.test_node(proxy)]

    def test_exit_nodes_parallel(self, exit_nodes: List[str], required_count: int) -> List[str]:            
        logger.info(f"Testing {len(exit_nodes)} exit nodes to find {required_count} working nodes...")
        
        working_nodes = []
        base_port = 30000
        
        batch_size = self.max_workers  # Increased batch size
        
        for i in range(0, len(exit_nodes), batch_size):
            if len(working_nodes) >= required_count:
                break
                
            batch = exit_nodes[i:i+batch_size]
            batch_num = i//batch_size + 1
            logger.info(f"Testing batch {batch_num}: {len(batch)} nodes (ports {base_port + i}-{base_port + i + len(batch) - 1})")
            
            try:
                batch_results = self._test_batch_directly(batch, base_port + i)
                working_nodes.extend(batch_results)
                logger.info(f"Batch {batch_num} completed: {len(batch_results)} working nodes found, total: {len(working_nodes)}")
            except Exception as e:
                logger.error(f"Error in batch {batch_num}: {e}")
                continue
        
        if len(working_nodes) < required_count:
            logger.warning(f"Found only {len(working_nodes)} working nodes, required {required_count}")
        else:
            logger.info(f"Successfully found {len(working_nodes)} working exit nodes")
        
        return working_nodes[:required_count]

    def cleanup(self):
        pass

    def _test_batch_directly(self, exit_nodes: List[str], base_port: int) -> List[str]:
        working_nodes = []
        batch_runner = TorParallelRunner(self.config_builder, max_concurrent=len(exit_nodes))
        
        try:
            ports = [base_port + i for i in range(len(exit_nodes))]
            exit_nodes_list = [[node] for node in exit_nodes]
            
            batch_runner.start_many(ports, exit_nodes_list)
            
            time.sleep(5)
            
            for i, (port, exit_node) in enumerate(zip(ports, exit_nodes)):
                try:
                    if port in batch_runner.instances:
                        instance = batch_runner.instances[port]
                        if instance.check_health():
                            proxy = instance.get_proxies()
                            if self.test_node(proxy):
                                working_nodes.append(exit_node)
                                logger.debug(f"Node {exit_node} passed all tests")
                            else:
                                logger.debug(f"Node {exit_node} failed proxy test")
                        else:
                            logger.debug(f"Node {exit_node} failed health check")
                    else:
                        logger.debug(f"Instance for node {exit_node} not found")
                except Exception as e:
                    logger.debug(f"Error testing node {exit_node}: {e}")
        
        finally:
            batch_runner.stop_all()
        
        return working_nodes

