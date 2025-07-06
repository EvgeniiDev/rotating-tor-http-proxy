import logging
import os
import threading
import time
from typing import List, Dict
import requests
import shutil
import glob
from queue import Queue

from tor_process import TorProcess
from config_manager import ConfigManager

logger = logging.getLogger(__name__)

class WorkerPool:
    def __init__(self, config_manager: ConfigManager, max_workers: int = 5):
        self.config_manager = config_manager
        self.max_workers = max_workers
        self.workers = []
        self.port_start = 30000
        self._lock = threading.Lock()
    
    def initialize_workers(self):
        with self._lock:
            if self.workers:
                return
            
            successful_workers = 0
            max_attempts = self.max_workers + 5
            
            for i in range(max_attempts):
                if successful_workers >= self.max_workers:
                    break
                    
                port = self.port_start + i
                worker = TorProcess(port=port, exit_nodes=[])
                
                try:
                    if worker.create_config(self.config_manager):
                        if worker.start_process():
                            if self._wait_for_worker_startup(worker):
                                self.workers.append(worker)
                                successful_workers += 1
                            else:
                                worker.stop_process()
                                worker.cleanup()
                        else:
                            worker.cleanup()
                    else:
                        worker.cleanup()
                except Exception:
                    worker.cleanup()
                
                if successful_workers < self.max_workers and i < max_attempts - 1:
                    time.sleep(1)
    
    def _wait_for_worker_startup(self, worker: TorProcess, timeout: int = 15) -> bool:
        start_time = time.time()
        connection_attempts = 0
        max_attempts = 20
        
        while time.time() - start_time < timeout and connection_attempts < max_attempts:
            connection_attempts += 1
            
            if worker.process and worker.process.poll() is not None:
                return False
            
            try:
                if worker.test_connection():
                    return True
            except Exception:
                pass
            
            time.sleep(0.5)
        
        return False
    
    def cleanup_workers(self):
        with self._lock:
            for worker in self.workers:
                worker.stop_process()
                worker.cleanup()
            self.workers.clear()

class ExitNodeTester:
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.test_url = "https://steamcommunity.com/market/search?appid=730"
        self.required_success_count = 3
        self.test_requests_count = 6
        self.max_workers = 10
        self.timeout = 60
        self._port_counter = 30000
        self._port_lock = threading.Lock()
        self._used_ports = set()
        self.worker_pool = WorkerPool(config_manager, self.max_workers)
        
    def __del__(self):
        self.worker_pool.cleanup_workers()
        self._cleanup_temp_files()
    
    def test_exit_nodes(self, exit_nodes: List[str]) -> List[str]:
        if not exit_nodes:
            return []
            
        logger.info(f"Testing {len(exit_nodes)} exit nodes with {self.max_workers} workers")
        
        try:
            self.worker_pool.initialize_workers()
            working_nodes = self._test_nodes_parallel(exit_nodes)
            
            total_nodes = len(exit_nodes)
            working_count = len(working_nodes)
            success_rate = (working_count / total_nodes * 100) if total_nodes > 0 else 0
            
            logger.info(f"Testing completed: {working_count}/{total_nodes} nodes passed ({success_rate:.1f}%)")
            
            return working_nodes
        finally:
            self.worker_pool.cleanup_workers()
    
    def _cleanup_temp_files(self):
        data_dir = os.path.expanduser('~/tor-http-proxy/data')
        if not os.path.exists(data_dir):
            return
        
        cleanup_count = 0
        try:
            temp_patterns = [
                os.path.join(data_dir, 'data_3*'),
                os.path.join(data_dir, 'torrc.*'),
                '/tmp/tor_*'
            ]
            
            for pattern in temp_patterns:
                for path in glob.glob(pattern):
                    try:
                        if os.path.isdir(path):
                            shutil.rmtree(path)
                        else:
                            os.unlink(path)
                        cleanup_count += 1
                    except Exception:
                        pass
                        
        except Exception:
            pass
    
    def _get_unique_test_port(self) -> int:
        with self._port_lock:
            port = self._port_counter
            self._port_counter += 1
            self._used_ports.add(port)
            return port
    
    def _release_port(self, port: int):
        with self._port_lock:
            self._used_ports.discard(port)
    
    def _test_nodes_parallel(self, exit_nodes: List[str]) -> List[str]:
        if not exit_nodes:
            return []
        
        self.worker_pool.initialize_workers()
        
        working_nodes = []
        lock = threading.Lock()
        total_nodes = len(exit_nodes)
        tested_count = 0
        progress_lock = threading.Lock()
        
        node_queue = Queue()
        for node in exit_nodes:
            node_queue.put(node)
        
        def worker_thread(worker_instance: TorProcess, worker_id: int):
            nonlocal tested_count
            
            while True:
                try:
                    node_ip = node_queue.get(timeout=1)
                except:
                    break
                
                try:
                    if not worker_instance.reload_exit_nodes([node_ip], self.config_manager):
                        continue
                    
                    time.sleep(3)
                    
                    if not self._wait_for_connection_ready(worker_instance):
                        continue
                    
                    if self._test_single_node_requests(worker_instance, node_ip):
                        with lock:
                            working_nodes.append(node_ip)
                    
                    with progress_lock:
                        tested_count += 1
                    
                except Exception:
                    pass
                finally:
                    node_queue.task_done()
        
        threads = []
        for i, worker_instance in enumerate(self.worker_pool.workers):
            thread = threading.Thread(target=worker_thread, args=(worker_instance, i))
            thread.start()
            threads.append(thread)
        
        node_queue.join()
        
        for thread in threads:
            thread.join()
        
        return working_nodes
    
    def _wait_for_startup(self, instance: TorProcess, timeout: int = 120) -> bool:
        logger.info(f"Waiting for Tor process on port {instance.port} to start up...")
        start_time = time.time()
        
        logger.debug(f"Process PID: {instance.process.pid if instance.process else 'None'}")
        logger.debug(f"Config file: {instance.config_file}")
        
        while time.time() - start_time < timeout:
            elapsed = time.time() - start_time
            
            if instance.process and instance.process.poll() is not None:
                logger.error(f"Tor process on port {instance.port} died during startup (exit code: {instance.process.returncode})")
                if instance.process.stdout:
                    stdout = instance.process.stdout.read().decode('utf-8', errors='ignore')
                    if stdout:
                        logger.error(f"Tor stdout: {stdout}")
                if instance.process.stderr:
                    stderr = instance.process.stderr.read().decode('utf-8', errors='ignore')
                    if stderr:
                        logger.error(f"Tor stderr: {stderr}")
                return False
                
            logger.debug(f"Testing connection at {elapsed:.1f}s...")
            connection_result = instance.test_connection()
            logger.debug(f"Connection test result: {connection_result}")
            
            if connection_result:
                logger.info(f"Tor process on port {instance.port} is ready after {elapsed:.1f}s")
                return True
                
            if elapsed > 0 and int(elapsed) % 15 == 0:
                logger.info(f"Still waiting for Tor startup on port {instance.port}... ({elapsed:.0f}s elapsed)")
            
            time.sleep(3)
        
        logger.warning(f"Tor process on port {instance.port} failed to start within {timeout}s")
        return False
    
    def _wait_for_connection_ready(self, instance: TorProcess, max_wait: int = 30) -> bool:
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            if instance.test_connection():
                return True
            time.sleep(2)
        
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
                    
            except:
                continue
        
        success = success_count >= self.required_success_count
        
        if success:
            logger.info(f"Node {node_ip} PASSED: {success_count}/{self.test_requests_count} requests")
        else:
            logger.warning(f"Node {node_ip} FAILED: {success_count}/{self.test_requests_count} requests")
        
        return success
    
    def test_and_filter_nodes(self, exit_nodes: List[str]) -> List[str]:
        working_nodes = self.test_exit_nodes(exit_nodes)
        self._cleanup_temp_files()
        return working_nodes
    
    def get_test_stats(self) -> Dict:
        return {
            'test_url': self.test_url,
            'required_success_count': self.required_success_count,
            'test_requests_count': self.test_requests_count,
            'max_workers': self.max_workers,
            'timeout': self.timeout
        }