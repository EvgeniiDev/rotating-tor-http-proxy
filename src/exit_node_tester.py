import logging
import os
import threading
import time
import subprocess
from typing import List
import requests
import shutil
import glob
from queue import Queue

from tor_process import TorProcess
from config_manager import ConfigManager
from parallel_worker_manager import ParallelWorkerManager

logger = logging.getLogger(__name__)

class ExitNodeTester:
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.test_url = "https://steamcommunity.com/market/search?appid=730"
        self.required_success_count = 2
        self.test_requests_count = 4
        self.max_workers = 25
        self.timeout = 20
        self.max_test_time = 1800
        self.parallel_manager = ParallelWorkerManager(port_start=30100)
        self.workers = []
        self._lock = threading.Lock()
        self._cleanup_existing_processes()
        
    def __del__(self):
        self._cleanup_workers()
        self._cleanup_temp_files()
    
    def _cleanup_existing_processes(self):
        subprocess.run(['pkill', '-f', f'tor.*3[0-9][0-9][0-9][0-9]'], capture_output=True, timeout=5)
        time.sleep(2)
        self._cleanup_temp_files()
    
    def _initialize_workers(self):
        with self._lock:
            if self.workers:
                return
            
            logger.info("Initializing worker pool...")
            ports = self.parallel_manager.find_free_ports(self.max_workers)
            
            if len(ports) < self.max_workers:
                logger.warning(f"Only found {len(ports)} free ports out of {self.max_workers} needed")
            
            self.workers = self.parallel_manager.create_workers_parallel(
                self.config_manager, ports, self.max_workers
            )
            
            logger.info(f"Successfully initialized {len(self.workers)}/{self.max_workers} workers")
    
    def _cleanup_workers(self):
        with self._lock:
            for worker in self.workers:
                worker.stop_process()
                worker.cleanup()
            self.workers.clear()

        
    def test_exit_nodes(self, exit_nodes: List[str]) -> List[str]:
        if not exit_nodes:
            return []
            
        logger.info(f"Testing {len(exit_nodes)} exit nodes with {self.max_workers} workers")
        start_time = time.time()
        
        self._initialize_workers()
        working_nodes = self._test_nodes_parallel(exit_nodes, start_time)
        self._cleanup_workers()
        
        working_count = len(working_nodes)
        success_rate = (working_count / len(exit_nodes) * 100) if exit_nodes else 0
        elapsed_time = time.time() - start_time
        
        logger.info(f"Testing completed in {elapsed_time:.1f}s: {working_count}/{len(exit_nodes)} nodes passed ({success_rate:.1f}%)")
        return working_nodes
    
    def _cleanup_temp_files(self):
        for pattern in ['/tmp/tor_3*', '/tmp/tor_*.torrc']:
            for path in glob.glob(pattern):
                if os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    os.unlink(path) if os.path.exists(path) else None
    
    def _test_nodes_parallel(self, exit_nodes: List[str], start_time: float) -> List[str]:
        working_nodes = []
        lock = threading.Lock()
        tested_count = 0
        progress_lock = threading.Lock()
        
        node_queue = Queue()
        for node in exit_nodes:
            node_queue.put(node)
        
        def worker_thread(worker_instance: TorProcess, worker_id: int):
            nonlocal tested_count
            
            while time.time() - start_time <= self.max_test_time:
                try:
                    node_ip = node_queue.get(timeout=2)
                except:
                    break
                
                if (worker_instance.reload_exit_nodes([node_ip], self.config_manager) and
                    self._wait_for_connection_ready(worker_instance) and
                    self._test_single_node_requests(worker_instance, node_ip)):
                    with lock:
                        working_nodes.append(node_ip)
                
                with progress_lock:
                    tested_count += 1
                    if tested_count % 10 == 0:
                        elapsed = time.time() - start_time
                        logger.info(f"Progress: {tested_count}/{len(exit_nodes)} nodes tested ({elapsed:.1f}s elapsed)")
                
                node_queue.task_done()
        
        threads = []
        for i, worker_instance in enumerate(self.workers):
            thread = threading.Thread(target=worker_thread, args=(worker_instance, i))
            thread.daemon = True
            thread.start()
            threads.append(thread)
        
        node_queue.join()
        
        for thread in threads:
            thread.join(timeout=5)
        
        return working_nodes
    
    def _wait_for_connection_ready(self, instance: TorProcess, max_wait: int = 10) -> bool:
        start_time = time.time()
        time.sleep(1)
        
        while time.time() - start_time < max_wait:
            if instance.test_connection():
                return True
            time.sleep(0.5)
        
        return False

    def _test_single_node_requests(self, instance: TorProcess, node_ip: str) -> bool:
        success_count = 0
        
        for i in range(self.test_requests_count):
            try:
                response = requests.get(
                    self.test_url,
                    proxies=instance.get_proxies(),
                    timeout=self.timeout
                )
                
                if response.status_code == 200:
                    success_count += 1
                    if success_count >= self.required_success_count:
                        logger.info(f"Node {node_ip} PASSED early: {success_count}/{i+1} requests")
                        return True
                    
            except:
                continue
        
        success = success_count >= self.required_success_count
        status = "PASSED" if success else "FAILED"
        logger.info(f"Node {node_ip} {status}: {success_count}/{self.test_requests_count} requests")
        return success
    
    def get_test_stats(self) -> dict:
        return {
            'max_workers': self.max_workers,
            'current_workers': len(self.workers),
            'test_url': self.test_url,
            'success_threshold': f"{self.required_success_count}/{self.test_requests_count}",
            'timeout': self.timeout
        }
