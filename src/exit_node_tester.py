import requests
import logging
import time
import threading
from typing import List
from tor_parallel_runner import TorParallelRunner
from concurrent.futures import ThreadPoolExecutor, as_completed

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
        self.base_port = 31000
        self._executor = None
        self._shutdown_event = threading.Event()

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

    def test_exit_nodes_parallel(self, exit_nodes: List[str], required_count: int) -> List[List[str]]:
        """
        Тестирует все доступные exit-ноды и распределяет их между торами.
        
        Args:
            exit_nodes: Список всех доступных exit-нод для тестирования
            required_count: Количество торов, для которых нужно найти ноды
            
        Returns:
            Список списков exit-нод для каждого тора
        """
        target_nodes_per_tor = 6
        total_target_nodes = required_count * target_nodes_per_tor
        
        logger.info(f"Testing ALL {len(exit_nodes)} exit nodes for {required_count} Tor processes")
        logger.info(f"Target: {target_nodes_per_tor} nodes per Tor ({total_target_nodes} total)")
        
        working_nodes = []
        total_tested = 0
        
        if self.batch_runner is None:
            self.batch_runner = TorParallelRunner(self.config_builder, max_workers=self.max_workers)
            self._initialize_tor_instances()
        
        try:
            total_batches = (len(exit_nodes) + self.max_workers - 1) // self.max_workers
            
            for i in range(0, len(exit_nodes), self.max_workers):
                batch = exit_nodes[i:i+self.max_workers]
                batch_num = i//self.max_workers + 1
                logger.info(f"Testing batch {batch_num}/{total_batches}: {len(batch)} nodes")
                
                try:
                    batch_results = self._test_batch_with_reconfigure(batch)
                    working_nodes.extend(batch_results)
                    total_tested += len(batch)
                    success_rate = len(working_nodes) / total_tested * 100 if total_tested > 0 else 0
                    
                    logger.info(f"Batch {batch_num} completed: {len(batch_results)}/{len(batch)} passed")
                    logger.info(f"Progress: {len(working_nodes)}/{total_tested} total working nodes ({success_rate:.1f}%)")
                    
                    if len(working_nodes) >= total_target_nodes:
                        logger.info(f"✅ Target reached: {len(working_nodes)}/{total_target_nodes} nodes found")
                    
                except Exception as e:
                    logger.error(f"Error in batch {batch_num}: {e}")
                    total_tested += len(batch)
                    continue
        finally:
            self.cleanup()
        
        return self._distribute_nodes_among_tors(working_nodes, required_count, target_nodes_per_tor)

    def cleanup(self):
        self._shutdown_event.set()
        
        if self._executor:
            logger.info("Shutting down ExitNodeChecker thread pool...")
            self._executor.shutdown(wait=True)
            self._executor = None
            
        if self.batch_runner:
            logger.info("Cleaning up ExitNodeChecker test pool...")
            try:
                self.batch_runner.shutdown()
                logger.info("All Tor test instances stopped")
            except Exception as e:
                logger.error(f"Error stopping Tor instances: {e}")
            finally:
                self.batch_runner = None
                logger.info("ExitNodeChecker test pool cleaned up successfully")

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass

    def _initialize_tor_instances(self):
        ports = [self.base_port + i for i in range(self.max_workers)]
        exit_nodes_list = [[]] * self.max_workers
        logger.info(f"Initializing {len(ports)} Tor instances on ports: {ports}")
        self.batch_runner.start_many(ports, exit_nodes_list)
        
        logger.info(f"Started instances: {list(self.batch_runner.instances.keys())}")
        for port, instance in self.batch_runner.instances.items():
            status = instance.get_status()
            logger.info(f"Port {port}: running={instance.is_running}, status={status}")

    def _test_batch_with_reconfigure(self, exit_nodes: List[str]) -> List[str]:
        logger.info(f"Testing batch with {len(exit_nodes)} exit nodes using parallel reconfiguration")
        logger.info(f"Available instances: {list(self.batch_runner.instances.keys())}")
        
        if self._shutdown_event.is_set():
            logger.warning("Shutdown event set, skipping batch test")
            return []
        
        test_tasks = []
        for i, exit_node in enumerate(exit_nodes):
            if i >= len(self.batch_runner.instances):
                break
            port = self.base_port + i
            if port in self.batch_runner.instances:
                instance = self.batch_runner.instances[port]
                if instance.is_running:
                    test_tasks.append((exit_node, port, instance))
                else:
                    logger.warning(f"Instance on port {port} is not running, skipping {exit_node}")
        
        if not test_tasks:
            logger.warning("No available instances for testing")
            return []
        
        logger.info(f"Starting parallel testing of {len(test_tasks)} exit nodes")
        
        working_nodes = []
        max_workers = min(len(test_tasks), self.max_workers)
        
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ExitTester")
        
        try:
            future_to_node = {}
            
            for exit_node, port, instance in test_tasks:
                if self._shutdown_event.is_set():
                    break
                future = self._executor.submit(self._test_single_node, exit_node, port, instance)
                future_to_node[future] = exit_node
            
            completed_count = 0
            for future in as_completed(future_to_node):
                if self._shutdown_event.is_set():
                    break
                    
                exit_node = future_to_node[future]
                completed_count += 1
                
                try:
                    result = future.result(timeout=30)
                    if result:
                        working_nodes.append(exit_node)
                        logger.info(f"✅ Test {completed_count}/{len(test_tasks)}: Node {exit_node} PASSED")
                    else:
                        logger.warning(f"❌ Test {completed_count}/{len(test_tasks)}: Node {exit_node} FAILED")
                except Exception as e:
                    logger.error(f"❌ Test {completed_count}/{len(test_tasks)}: Node {exit_node} ERROR: {e}")
        
        except Exception as e:
            logger.error(f"Error during parallel testing: {e}")
        
        logger.info(f"Parallel batch testing completed: {len(working_nodes)}/{len(test_tasks)} nodes passed testing")
        return working_nodes
    
    def _test_single_node(self, exit_node: str, port: int, instance) -> bool:
        if not instance.reconfigure([exit_node]):
            logger.warning(f"Failed to reconfigure instance for node {exit_node}")
            return False
        
        if not instance.check_health():
            logger.warning(f"Health check failed for node {exit_node}")
            return False
    
        proxy = instance.get_proxies()
        return self.test_node(proxy)

    def _distribute_nodes_among_tors(self, working_nodes: List[str], tor_count: int, target_per_tor: int) -> List[List[str]]:
        """
        Равномерно распределяет найденные exit-ноды между торами.
        
        Args:
            working_nodes: Список всех найденных рабочих exit-нод
            tor_count: Количество торов
            target_per_tor: Целевое количество нод на тор
            
        Returns:
            Список списков exit-нод для каждого тора
        """
        if not working_nodes:
            logger.warning("No working nodes found for distribution")
            return [[] for _ in range(tor_count)]
        
        total_found = len(working_nodes)
        total_target = tor_count * target_per_tor
        
        logger.info(f"📊 Distributing {total_found} working nodes among {tor_count} Tor processes")
        
        if total_found >= total_target:
            logger.info(f"✅ Sufficient nodes found: {total_found}/{total_target}")
            nodes_per_tor = target_per_tor
        else:
            nodes_per_tor = total_found // tor_count
            extra_nodes = total_found % tor_count
            logger.warning(f"⚠️ Insufficient nodes: {total_found}/{total_target}")
            logger.info(f"📋 Distribution plan: {nodes_per_tor} nodes per Tor + {extra_nodes} extra")
        
        distributed_nodes = []
        node_index = 0
        
        for tor_index in range(tor_count):
            tor_nodes = []
            
            if total_found >= total_target:
                nodes_for_this_tor = nodes_per_tor
            else:
                nodes_for_this_tor = nodes_per_tor + (1 if tor_index < extra_nodes else 0)
            
            for _ in range(nodes_for_this_tor):
                if node_index < len(working_nodes):
                    tor_nodes.append(working_nodes[node_index])
                    node_index += 1
            
            distributed_nodes.append(tor_nodes)
            logger.info(f"🔹 Tor {tor_index + 1}: {len(tor_nodes)} exit nodes assigned")
        
        used_nodes = sum(len(nodes) for nodes in distributed_nodes)
        logger.info(f"📈 Distribution complete: {used_nodes}/{total_found} nodes assigned")
        
        return distributed_nodes

    def scan_all_exit_nodes(self, test_requests_count: int = 6, required_success_count: int = 3) -> dict:
        """
        Сканирует все доступные exit-ноды Tor и разделяет их на два списка:
        - passed_nodes: ноды, которые прошли проверку (3+ успешных запроса из 6)
        - failed_nodes: ноды, которые не прошли проверку
        
        Args:
            test_requests_count: Количество тестовых запросов к каждой ноде
            required_success_count: Минимальное количество успешных запросов для прохождения теста
            
        Returns:
            dict: {"passed_nodes": List[str], "failed_nodes": List[str], "stats": dict}
        """
        from tor_relay_manager import TorRelayManager
        
        self.test_requests_count = test_requests_count
        self.required_success_count = required_success_count
        
        logger.info(f"🚀 Starting comprehensive exit node scan")
        logger.info(f"📊 Test parameters: {test_requests_count} requests, {required_success_count} required successes")
        
        relay_manager = TorRelayManager()
        
        logger.info("📡 Fetching current Tor relay information...")
        relay_data = relay_manager.fetch_tor_relays()
        if not relay_data:
            logger.error("Failed to fetch Tor relay data")
            return {"passed_nodes": [], "failed_nodes": [], "stats": {"error": "Failed to fetch relay data"}}
        
        exit_nodes_info = relay_manager.extract_relay_ips(relay_data)
        if not exit_nodes_info:
            logger.error("No exit nodes found")
            return {"passed_nodes": [], "failed_nodes": [], "stats": {"error": "No exit nodes found"}}
        
        exit_node_ips = [node['ip'] for node in exit_nodes_info]
        total_nodes = len(exit_node_ips)
        
        logger.info(f"🌐 Found {total_nodes} qualified exit nodes to test")
        
        passed_nodes = []
        failed_nodes = []
        tested_count = 0
        
        if self.batch_runner is None:
            self.batch_runner = TorParallelRunner(self.config_builder, max_workers=self.max_workers)
            self._initialize_tor_instances()
        
        try:
            total_batches = (total_nodes + self.max_workers - 1) // self.max_workers
            
            for i in range(0, total_nodes, self.max_workers):
                batch = exit_node_ips[i:i+self.max_workers]
                batch_num = i//self.max_workers + 1
                
                logger.info(f"🔄 Processing batch {batch_num}/{total_batches}: {len(batch)} nodes")
                
                try:
                    batch_passed, batch_failed = self._scan_batch_detailed(batch)
                    passed_nodes.extend(batch_passed)
                    failed_nodes.extend(batch_failed)
                    tested_count += len(batch)
                    
                    success_rate = len(passed_nodes) / tested_count * 100 if tested_count > 0 else 0
                    
                    logger.info(f"✅ Batch {batch_num}: {len(batch_passed)} passed, {len(batch_failed)} failed")
                    logger.info(f"📈 Progress: {tested_count}/{total_nodes} tested ({tested_count/total_nodes*100:.1f}%)")
                    logger.info(f"🎯 Success rate: {len(passed_nodes)}/{tested_count} ({success_rate:.1f}%)")
                    
                except Exception as e:
                    logger.error(f"❌ Error in batch {batch_num}: {e}")
                    failed_nodes.extend(batch)
                    tested_count += len(batch)
                    continue
        finally:
            self.cleanup()
        
        stats = {
            "total_tested": tested_count,
            "passed_count": len(passed_nodes),
            "failed_count": len(failed_nodes),
            "success_rate": len(passed_nodes) / tested_count * 100 if tested_count > 0 else 0,
            "test_parameters": {
                "requests_per_node": test_requests_count,
                "required_successes": required_success_count,
                "test_url": self.test_url
            }
        }
        
        logger.info(f"🏁 Scan complete!")
        logger.info(f"📊 Results: {len(passed_nodes)} passed, {len(failed_nodes)} failed")
        logger.info(f"📈 Overall success rate: {stats['success_rate']:.1f}%")
        
        return {
            "passed_nodes": passed_nodes,
            "failed_nodes": failed_nodes,
            "stats": stats
        }
    
    def _scan_batch_detailed(self, exit_nodes: List[str]) -> tuple:
        """
        Детально тестирует батч exit-нод и возвращает списки прошедших и не прошедших ноды.
        
        Returns:
            tuple: (passed_nodes, failed_nodes)
        """
        if self._shutdown_event.is_set():
            logger.warning("Shutdown event set, marking all nodes as failed")
            return [], exit_nodes
            
        test_tasks = []
        for i, exit_node in enumerate(exit_nodes):
            if i >= len(self.batch_runner.instances):
                break
            port = self.base_port + i
            if port in self.batch_runner.instances:
                instance = self.batch_runner.instances[port]
                if instance.is_running:
                    test_tasks.append((exit_node, port, instance))
                else:
                    logger.warning(f"Instance on port {port} is not running, marking {exit_node} as failed")
        
        if not test_tasks:
            logger.warning("No available instances for testing")
            return [], exit_nodes
        
        passed_nodes = []
        failed_nodes = []
        max_workers = min(len(test_tasks), self.max_workers)
        
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ExitScanner")
        
        try:
            future_to_node = {}
            
            for exit_node, port, instance in test_tasks:
                if self._shutdown_event.is_set():
                    break
                future = self._executor.submit(self._test_single_node_detailed, exit_node, port, instance)
                future_to_node[future] = exit_node
            
            completed_count = 0
            for future in as_completed(future_to_node):
                if self._shutdown_event.is_set():
                    break
                    
                exit_node = future_to_node[future]
                completed_count += 1
                
                try:
                    result = future.result(timeout=60)
                    if result:
                        passed_nodes.append(exit_node)
                        logger.info(f"✅ Test {completed_count}/{len(test_tasks)}: {exit_node} PASSED")
                    else:
                        failed_nodes.append(exit_node)
                        logger.warning(f"❌ Test {completed_count}/{len(test_tasks)}: {exit_node} FAILED")
                except Exception as e:
                    failed_nodes.append(exit_node)
                    logger.error(f"❌ Test {completed_count}/{len(test_tasks)}: {exit_node} ERROR: {e}")
        
        except Exception as e:
            logger.error(f"Error during detailed scanning: {e}")
        
        untested_nodes = [node for node, _, _ in test_tasks[len(test_tasks):]]
        if untested_nodes:
            failed_nodes.extend(untested_nodes)
            logger.warning(f"Marked {len(untested_nodes)} untested nodes as failed")
        
        return passed_nodes, failed_nodes
    
    def _test_single_node_detailed(self, exit_node: str, port: int, instance) -> bool:
        """
        Детально тестирует одну exit-ноду с подробным логированием.
        """
        logger.debug(f"🔍 Starting detailed test for node {exit_node}")
        
        if not instance.reconfigure([exit_node]):
            logger.warning(f"Failed to reconfigure instance for node {exit_node}")
            return False
        
        if not instance.check_health():
            logger.warning(f"Health check failed for node {exit_node}")
            return False
    
        proxy = instance.get_proxies()
        
        success_count = 0
        for request_num in range(self.test_requests_count):
            try:
                response = requests.get(self.test_url, proxies=proxy, timeout=self.timeout)
                if response.status_code == 200:
                    success_count += 1
                    logger.debug(f"🌐 {exit_node} request {request_num + 1}/{self.test_requests_count}: SUCCESS")
                    if success_count >= self.required_success_count:
                        logger.debug(f"🎯 {exit_node} reached required success threshold early")
                        return True
                else:
                    logger.debug(f"⚠️ {exit_node} request {request_num + 1}/{self.test_requests_count}: HTTP {response.status_code}")
            except Exception as e:
                logger.debug(f"💥 {exit_node} request {request_num + 1}/{self.test_requests_count}: ERROR {e}")
                continue
        
        result = success_count >= self.required_success_count
        logger.debug(f"📋 {exit_node} final result: {success_count}/{self.test_requests_count} successes - {'PASSED' if result else 'FAILED'}")
        return result

    def save_scan_results(self, scan_results: dict, filename_prefix: str = "exit_nodes_scan") -> dict:
        """
        Сохраняет результаты сканирования в файлы.
        
        Args:
            scan_results: Результаты сканирования от scan_all_exit_nodes
            filename_prefix: Префикс для имен файлов
            
        Returns:
            dict: Пути к созданным файлам
        """
        import json
        from datetime import datetime
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        passed_file = f"{filename_prefix}_passed_{timestamp}.json"
        failed_file = f"{filename_prefix}_failed_{timestamp}.json"
        stats_file = f"{filename_prefix}_stats_{timestamp}.json"
        
        try:
            with open(passed_file, 'w') as f:
                json.dump({
                    "timestamp": timestamp,
                    "count": len(scan_results["passed_nodes"]),
                    "nodes": scan_results["passed_nodes"],
                    "test_parameters": scan_results["stats"]["test_parameters"]
                }, f, indent=2)
            
            with open(failed_file, 'w') as f:
                json.dump({
                    "timestamp": timestamp,
                    "count": len(scan_results["failed_nodes"]),
                    "nodes": scan_results["failed_nodes"],
                    "test_parameters": scan_results["stats"]["test_parameters"]
                }, f, indent=2)
            
            with open(stats_file, 'w') as f:
                json.dump({
                    "scan_timestamp": timestamp,
                    "statistics": scan_results["stats"]
                }, f, indent=2)
            
            logger.info(f"📁 Results saved:")
            logger.info(f"   Passed nodes: {passed_file}")
            logger.info(f"   Failed nodes: {failed_file}")
            logger.info(f"   Statistics: {stats_file}")
            
            return {
                "passed_file": passed_file,
                "failed_file": failed_file,
                "stats_file": stats_file
            }
            
        except Exception as e:
            logger.error(f"Failed to save scan results: {e}")
            return {}
