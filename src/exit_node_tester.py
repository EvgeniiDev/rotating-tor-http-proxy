import requests
import logging
import threading
import queue
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

    def __init__(self, config_builder, max_workers: int, test_url: str = "https://steamcommunity.com/market/search?appid=730",
                 test_requests_count: int = 2, required_success_count: int = 1,
                 timeout: int = 30):
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
        for request_num in range(self.test_requests_count):
            try:
                response = requests.get(
                    self.test_url, proxies=proxy, timeout=self.timeout)
                if response.status_code == 200:
                    success_count += 1
                    if success_count >= self.required_success_count:
                        logger.info(
                            f"Node test PASSED: {success_count}/{self.test_requests_count} successful requests")
                        return True
            except Exception as e:
                continue

        result = success_count >= self.required_success_count
        logger.info(
            f"Node test {'PASSED' if result else 'FAILED'}: {success_count}/{self.test_requests_count} successful requests")
        return result

    def test_exit_nodes_parallel(self, exit_nodes: List[str], required_count: int) -> List[List[str]]:
        target_nodes_per_tor = 6
        total_target_nodes = required_count * target_nodes_per_tor

        logger.info(
            f"Testing ALL {len(exit_nodes)} exit nodes for {required_count} Tor processes")
        logger.info(
            f"Target: {target_nodes_per_tor} nodes per Tor ({total_target_nodes} total)")

        if self.batch_runner is None:
            self.batch_runner = TorParallelRunner(
                self.config_builder, max_workers=self.max_workers)
            self._initialize_tor_instances()

        working_nodes = []
        tested_count = 0
        nodes_queue = queue.Queue()
        results_queue = queue.Queue()
        stats_lock = threading.Lock()

        for node in exit_nodes:
            nodes_queue.put(node)

        available_instances = list(self.batch_runner.instances.items())

        def worker_thread(instance_id: str):
            port, instance = available_instances[instance_id]
            local_tested = 0
            local_working = []

            while not self._shutdown_event.is_set():
                try:
                    exit_node = nodes_queue.get(timeout=1)
                except queue.Empty:
                    break

                try:
                    if self._test_single_node(exit_node, port, instance):
                        local_working.append(exit_node)

                    local_tested += 1

                    with stats_lock:
                        nonlocal tested_count
                        tested_count += 1
                        current_working = len(
                            working_nodes) + len(local_working)
                        success_rate = current_working / tested_count * 100 if tested_count > 0 else 0

                        if tested_count % 50 == 0 or tested_count <= 10:
                            logger.info(
                                f"Progress: {current_working}/{tested_count} working nodes ({success_rate:.1f}%)")

                        if current_working >= total_target_nodes:
                            logger.info(
                                f"✅ Target reached: {current_working}/{total_target_nodes} nodes found")

                except Exception as e:
                    logger.error(
                        f"Worker {instance_id} error testing {exit_node}: {e}")
                    local_tested += 1
                    with stats_lock:
                        tested_count += 1
                finally:
                    nodes_queue.task_done()

            results_queue.put((local_tested, local_working))
            logger.info(
                f"Worker {instance_id} finished: {len(local_working)}/{local_tested} nodes passed")

        try:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.max_workers, thread_name_prefix="ExitTester")

            futures = []
            for i in range(min(len(available_instances), self.max_workers)):
                future = self._executor.submit(worker_thread, i)
                futures.append(future)

            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"Worker thread error: {e}")

            while not results_queue.empty():
                local_tested, local_working = results_queue.get()
                working_nodes.extend(local_working)

            final_success_rate = len(working_nodes) / \
                tested_count * 100 if tested_count > 0 else 0
            logger.info(
                f"🏁 Testing complete: {len(working_nodes)}/{tested_count} total working nodes ({final_success_rate:.1f}%)")

        except Exception as e:
            logger.error(f"Error in parallel testing: {e}")
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
                logger.info(
                    "ExitNodeChecker test pool cleaned up successfully")

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass

    def _initialize_tor_instances(self):
        ports = [self.base_port + i for i in range(self.max_workers)]
        exit_nodes_list = [[]] * self.max_workers
        logger.info(
            f"Initializing {len(ports)} Tor instances on ports: {ports}")
        self.batch_runner.start_many(ports, exit_nodes_list)

        logger.info(
            f"Started instances: {list(self.batch_runner.instances.keys())}")
        for port, instance in self.batch_runner.instances.items():
            logger.info(f"Port {port}: running={instance.is_running}")

    def _test_single_node(self, exit_node: str, port: int, instance) -> bool:
        if not instance.reconfigure([exit_node]):
            logger.warning(
                f"Failed to reconfigure instance for node {exit_node}")
            return False

        if not instance.check_health():
            logger.warning(f"Health check failed for node {exit_node}")
            return False

        proxy = instance.get_proxies()
        return self.test_node(proxy)

    def _distribute_nodes_among_tors(self, working_nodes: List[str], tor_count: int, target_per_tor: int) -> List[List[str]]:
        if not working_nodes:
            logger.warning("No working nodes found for distribution")
            return [[] for _ in range(tor_count)]

        total_found = len(working_nodes)

        logger.info(
            f"📊 Distributing {total_found} working nodes among {tor_count} Tor processes using round-robin")

        distributed_nodes = [[] for _ in range(tor_count)]

        for node_index, node in enumerate(working_nodes):
            tor_index = node_index % tor_count
            distributed_nodes[tor_index].append(node)

        for tor_index in range(tor_count):
            logger.info(
                f"🔹 Tor {tor_index + 1}: {len(distributed_nodes[tor_index])} exit nodes assigned")

        used_nodes = sum(len(nodes) for nodes in distributed_nodes)
        logger.info(
            f"📈 Distribution complete: {used_nodes}/{total_found} nodes assigned")

        return distributed_nodes
