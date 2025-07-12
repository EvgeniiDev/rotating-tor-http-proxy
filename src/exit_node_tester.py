import requests
import time
import logging
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
from tor_process import TorInstance

logger = logging.getLogger(__name__)

class ExitNodeChecker:
    """
    Отвечает только за проверку пригодности exit-ноды для парсинга Steam.
    """
    def __init__(self, test_url: str = "https://steamcommunity.com/market/search?appid=730", 
                 test_requests_count: int = 6, required_success_count: int = 3, 
                 timeout: int = 20, config_builder=None, max_workers: int = 10):
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
        if not self.config_builder:
            logger.error("Config builder not provided for exit node testing")
            return []
            
        if not exit_nodes:
            logger.warning("No exit nodes provided for testing")
            return []
            
        logger.info(f"Testing {len(exit_nodes)} exit nodes using reconfigurable approach...")
        
        working_nodes = []
        num_instances = min(self.max_workers, len(exit_nodes))
        base_port = 30000
        
        try:
            instances = self._create_tor_instances(num_instances, base_port)
            
            with ThreadPoolExecutor(max_workers=num_instances) as executor:
                tasks = []
                for i, instance in enumerate(instances):
                    chunk_size = max(1, len(exit_nodes) // num_instances)
                    start_idx = i * chunk_size
                    end_idx = start_idx + chunk_size if i < num_instances - 1 else len(exit_nodes)
                    node_chunk = exit_nodes[start_idx:end_idx]
                    
                    max_results = max(1, required_count // num_instances + (1 if i < required_count % num_instances else 0))
                    task = executor.submit(self._test_nodes_with_reconfiguration, instance, node_chunk, max_results)
                    tasks.append(task)
                
                for task in as_completed(tasks):
                    try:
                        chunk_results = task.result()
                        working_nodes.extend(chunk_results)
                        if len(working_nodes) >= required_count:
                            break
                    except Exception as e:
                        logger.warning(f"Error in chunk testing: {e}")
            
        finally:
            self._cleanup_tor_instances(instances)
        
        logger.info(f"Found {len(working_nodes)} working exit nodes using reconfigurable approach")
        return working_nodes[:required_count]

    def _create_tor_instances(self, count: int, base_port: int) -> List[TorInstance]:
        instances = []
        for i in range(count):
            port = base_port + i
            instance = TorInstance(port, [], self.config_builder)
            instance.create_config()
            instance.start()
            time.sleep(2)
            instances.append(instance)
        return instances

    def _test_nodes_with_reconfiguration(self, tor_instance: TorInstance, exit_nodes: List[str], max_results: int) -> List[str]:
        working_nodes = []
        
        for exit_node in exit_nodes:
            if len(working_nodes) >= max_results:
                break
            try:
                if tor_instance.reconfigure([exit_node]):
                    time.sleep(5)
                    if tor_instance.check_health():
                        proxy = tor_instance.get_proxies()
                        if self.test_node(proxy):
                            working_nodes.append(exit_node)
                            logger.info(f"Node {exit_node} passed tests via reconfiguration")
            except Exception as e:
                logger.warning(f"Error testing node {exit_node} via reconfiguration: {e}")
        return working_nodes


    def _cleanup_tor_instances(self, instances: List[TorInstance]):
        for instance in instances:
            try:
                instance.stop()
            except Exception as e:
                logger.debug(f"Error stopping instance: {e}")

