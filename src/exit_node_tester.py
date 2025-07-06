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
            
            logger.info(f"Initializing workers for 5-thread processing...")
            successful_workers = 0
            max_attempts = 10
            
            for i in range(max_attempts):
                if successful_workers >= 5:
                    break
                    
                port = self.port_start + i
                worker = TorProcess(port=port, exit_nodes=[])
                
                try:
                    if worker.create_config(self.config_manager):
                        if worker.start_process():
                            if self._wait_for_worker_startup(worker):
                                self.workers.append(worker)
                                successful_workers += 1
                                logger.info(f"Worker {successful_workers}/5 ready on port {port}")
                            else:
                                worker.stop_process()
                                worker.cleanup()
                        else:
                            worker.cleanup()
                    else:
                        worker.cleanup()
                except Exception:
                    worker.cleanup()
            
            logger.info(f"Worker pool ready with {len(self.workers)} threads for parallel processing")
    
    def _wait_for_worker_startup(self, worker: TorProcess, timeout: int = 10) -> bool:
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if worker.test_connection():
                    return True
            except Exception:
                pass
            time.sleep(0.3)
        return False
    
    def cleanup_workers(self):
        with self._lock:
            for worker in self.workers:
                worker.stop_process()
                worker.cleanup()
            self.workers.clear()
            logger.info("Worker pool cleaned up")

class ExitNodeTester:
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.test_url = "https://steamcommunity.com/market/search?appid=730"
        self.required_success_count = 3
        self.test_requests_count = 6
        self.max_workers = 5
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
            logger.warning("No exit nodes provided for testing")
            return []
            
        logger.info(f"Starting testing of {len(exit_nodes)} exit nodes with 5 worker threads")
        
        self.worker_pool.initialize_workers()
        working_nodes = self._test_nodes_parallel(exit_nodes)
        
        total_nodes = len(exit_nodes)
        working_count = len(working_nodes)
        failure_count = total_nodes - working_count
        success_rate = (working_count / total_nodes * 100) if total_nodes > 0 else 0
        
        logger.info(f"Testing completed. {working_count}/{total_nodes} nodes passed ({success_rate:.1f}% success rate)")
        
        if failure_count > 0:
            logger.warning(f"{failure_count} nodes failed testing")
            if working_count == 0:
                logger.error("No working nodes found! This may indicate:")
                logger.error("- Network connectivity issues")
                logger.error("- Tor configuration problems")
                logger.error("- Target website blocking all exit nodes")
                logger.error("- Proxy configuration issues")
            elif success_rate < 30:
                logger.warning(f"Low success rate ({success_rate:.1f}%) may indicate systemic issues")
        
        return working_nodes
    
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
            
            if cleanup_count > 0:
                logger.info(f"Cleaned up {cleanup_count} temporary files/directories")
                        
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
        
        if not self.worker_pool.workers:
            logger.warning("No workers available in 5-thread pool, testing nodes sequentially")
            return self._test_nodes_sequential(exit_nodes)
        
        working_nodes = []
        lock = threading.Lock()
        total_nodes = len(exit_nodes)
        tested_count = 0
        progress_lock = threading.Lock()
        
        logger.info(f"Using {len(self.worker_pool.workers)} worker threads from 5-thread pool")
        
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
                    logger.debug(f"Worker {worker_id}: Testing node {node_ip}")
                    
                    if not worker_instance.reload_exit_nodes([node_ip], self.config_manager):
                        logger.warning(f"Worker {worker_id}: Failed to reload exit nodes for {node_ip}")
                        continue
                    
                    time.sleep(3)
                    
                    if not self._wait_for_connection_ready(worker_instance):
                        logger.warning(f"Worker {worker_id}: Connection not ready for {node_ip}")
                        continue
                    
                    if self._test_single_node_requests(worker_instance, node_ip):
                        logger.info(f"Node {node_ip} PASSED (worker {worker_id})")
                        with lock:
                            working_nodes.append(node_ip)
                    else:
                        logger.warning(f"Node {node_ip} FAILED (worker {worker_id})")
                    
                    with progress_lock:
                        tested_count += 1
                        progress = (tested_count / total_nodes) * 100
                        if tested_count % 10 == 0 or tested_count == total_nodes:
                            logger.info(f"Testing progress: {tested_count}/{total_nodes} nodes tested ({progress:.1f}%)")
                    
                except Exception as e:
                    logger.error(f"Worker {worker_id}: Error testing node {node_ip}: {e}")
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
    
    def _test_nodes_sequential(self, exit_nodes: List[str]) -> List[str]:
        logger.info("Testing nodes in sequential mode (5-thread fallback)")
        working_nodes = []
        
        for i, node in enumerate(exit_nodes):
            logger.info(f"Testing node {i+1}/{len(exit_nodes)}: {node}")
            
            port = self._get_unique_test_port()
            worker = TorProcess(port=port, exit_nodes=[node])
            
            try:
                if worker.create_config(self.config_manager):
                    if worker.start_process():
                        if self._wait_for_startup(worker, timeout=25):
                            if self._test_single_node_requests(worker, node):
                                working_nodes.append(node)
                                logger.info(f"Node {node} PASSED")
                            else:
                                logger.warning(f"Node {node} FAILED")
                        else:
                            logger.warning(f"Node {node} failed to start")
                    else:
                        logger.warning(f"Node {node} process failed to start")
                else:
                    logger.warning(f"Node {node} config creation failed")
            except Exception as e:
                logger.error(f"Error testing node {node}: {e}")
            finally:
                self._release_port(port)
                worker.stop_process()
                worker.cleanup()
        
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
        error_diagnostics = []
        
        for i in range(self.test_requests_count):
            request_errors = []
            try:
                response = requests.get(
                    self.test_url,
                    proxies=instance.get_proxies(),
                    timeout=self.timeout
                )
                
                response_size = len(response.content)
                
                logger.info(f"Node {node_ip}: Request {i+1} - Status: {response.status_code}, Size: {response_size} bytes")
                
                if response.status_code == 200:
                    success_count += 1
                    logger.info(f"Node {node_ip}: Request {i+1} successful (200)")
                else:
                    error_code = f"HTTP_{response.status_code}"
                    request_errors.append(error_code)
                    logger.warning(f"Node {node_ip}: Request {i+1} failed - {error_code}")
                    
            except requests.exceptions.ConnectTimeout as e:
                error_code = "CONNECT_TIMEOUT"
                request_errors.append(error_code)
                logger.warning(f"Node {node_ip}: Request {i+1} failed - {error_code}: {e}")
                continue
            except requests.exceptions.ReadTimeout as e:
                error_code = "READ_TIMEOUT"
                request_errors.append(error_code)
                logger.warning(f"Node {node_ip}: Request {i+1} failed - {error_code}: {e}")
                continue
            except requests.exceptions.ConnectionError as e:
                error_code = "CONNECTION_ERROR"
                request_errors.append(error_code)
                logger.warning(f"Node {node_ip}: Request {i+1} failed - {error_code}: {e}")
                continue
            except Exception as e:
                error_code = "UNEXPECTED_ERROR"
                request_errors.append(error_code)
                logger.error(f"Node {node_ip}: Request {i+1} failed - {error_code}: {e}")
                continue
            
            if request_errors:
                error_diagnostics.append(f"Req{i+1}: {','.join(request_errors)}")
        
        if error_diagnostics:
            logger.info(f"Node {node_ip}: Error diagnostics: {' | '.join(error_diagnostics)}")
        
        success = success_count >= self.required_success_count
        
        if success:
            logger.info(f"Node {node_ip} PASSED: {success_count}/{self.test_requests_count} requests")
        else:
            logger.warning(f"Node {node_ip} FAILED: {success_count}/{self.test_requests_count} requests")
        
        return success
    
    def test_and_filter_nodes(self, exit_nodes: List[str]) -> List[str]:
        start_time = time.time()
        working_nodes = self.test_exit_nodes(exit_nodes)
        elapsed_time = time.time() - start_time
        
        logger.info(f"Node testing completed in {elapsed_time:.2f} seconds")
        logger.info(f"Working nodes: {working_nodes}")
        
        return working_nodes
    
    def get_test_stats(self) -> Dict:
        return {
            'test_url': self.test_url,
            'required_success_count': self.required_success_count,
            'test_requests_count': self.test_requests_count,
            'max_workers': self.max_workers,
            'timeout': self.timeout
        }