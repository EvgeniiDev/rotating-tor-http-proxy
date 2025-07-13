import requests
import logging
import time
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
                 timeout: int = 30, config_builder=None, max_workers: int = 20):
        self.test_url = test_url
        self.test_requests_count = test_requests_count
        self.required_success_count = required_success_count
        self.timeout = timeout
        self.config_builder = config_builder
        self.max_workers = max_workers
        self.batch_runner = None
        self.base_port = 30000

    def test_node(self, proxy: dict) -> bool:
        success_count = 0
        logger.info(f"Testing proxy {proxy} with {self.test_requests_count} requests")
        
        for request_num in range(self.test_requests_count):
            try:
                response = requests.get(self.test_url, proxies=proxy, timeout=self.timeout)
                if response.status_code == 200:
                    success_count += 1
                    logger.info(f"Request {request_num + 1}/{self.test_requests_count}: SUCCESS (total: {success_count})")
                    if success_count >= self.required_success_count:
                        logger.info(f"Node test PASSED: {success_count}/{self.test_requests_count} successful requests")
                        return True
                else:
                    logger.warning(f"Request {request_num + 1}/{self.test_requests_count}: HTTP {response.status_code}")
            except Exception as e:
                logger.warning(f"Request {request_num + 1}/{self.test_requests_count}: ERROR {e}")
                continue
        
        result = success_count >= self.required_success_count
        logger.info(f"Node test {'PASSED' if result else 'FAILED'}: {success_count}/{self.test_requests_count} successful requests")
        return result

    def test_nodes(self, proxies: List[dict]) -> List[dict]:
        return [proxy for proxy in proxies if self.test_node(proxy)]

    def test_exit_nodes_parallel(self, exit_nodes: List[str], required_count: int) -> List[str]:            
        logger.info(f"Testing {len(exit_nodes)} exit nodes to find {required_count} working nodes...")
        
        working_nodes = []
        
        if self.batch_runner is None:
            self.batch_runner = TorParallelRunner(self.config_builder, max_concurrent=self.max_workers)
            self._initialize_tor_instances()
        
        for i in range(0, len(exit_nodes), self.max_workers):
            if len(working_nodes) >= required_count:
                break
                
            batch = exit_nodes[i:i+self.max_workers]
            batch_num = i//self.max_workers + 1
            logger.info(f"Testing batch {batch_num}: {len(batch)} nodes")
            
            try:
                batch_results = self._test_batch_with_reconfigure(batch)
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
        if self.batch_runner:
            self.batch_runner.stop_all()
            self.batch_runner = None

    def _initialize_tor_instances(self):
        ports = [self.base_port + i for i in range(self.max_workers)]
        exit_nodes_list = [[]] * self.max_workers
        logger.info(f"Initializing {len(ports)} Tor instances on ports: {ports}")
        self.batch_runner.start_many(ports, exit_nodes_list)
        
        time.sleep(5)
        
        logger.info(f"Started instances: {list(self.batch_runner.instances.keys())}")
        for port, instance in self.batch_runner.instances.items():
            status = instance.get_status()
            logger.info(f"Port {port}: running={instance.is_running}, status={status}")

    def _test_batch_with_reconfigure(self, exit_nodes: List[str]) -> List[str]:
        working_nodes = []
        
        try:
            logger.info(f"Testing batch with {len(exit_nodes)} exit nodes using reconfiguration")
            logger.info(f"Available instances: {list(self.batch_runner.instances.keys())}")
            
            for i, exit_node in enumerate(exit_nodes):
                if i >= self.max_workers:
                    break
                    
                port = self.base_port + i
                logger.info(f"Testing node {exit_node} on port {port}")
                
                if port in self.batch_runner.instances:
                    instance = self.batch_runner.instances[port]
                    logger.info(f"Found instance for port {port}")
                    
                    if not instance.is_running:
                        logger.warning(f"Instance on port {port} is not running, skipping")
                        continue
                    
                    if instance.reconfigure([exit_node]):
                        logger.info(f"Successfully reconfigured instance for node {exit_node}")

                        if instance.check_health():
                            logger.info(f"Health check passed for node {exit_node}")
                            proxy = instance.get_proxies()
                            logger.info(f"Testing proxy {proxy} for node {exit_node}")
                            
                            if self.test_node(proxy):
                                working_nodes.append(exit_node)
                                logger.info(f"✓ Node {exit_node} passed all tests")
                            else:
                                logger.warning(f"✗ Node {exit_node} failed proxy test")
                        else:
                            logger.warning(f"✗ Node {exit_node} failed health check")
                    else:
                        logger.warning(f"✗ Failed to reconfigure instance for node {exit_node}")
                else:
                    logger.error(f"Instance for port {port} not found in instances")
                    
        except Exception as e:
            logger.error(f"Error in batch reconfiguration: {e}", exc_info=True)
        
        logger.info(f"Batch testing completed, found {len(working_nodes)} working nodes")
        return working_nodes

