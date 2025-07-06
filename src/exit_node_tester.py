import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Set, Optional
import requests
from datetime import datetime

from tor_process import TorProcess
from config_manager import ConfigManager

logger = logging.getLogger(__name__)

class ExitNodeTester:
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.test_url = "https://steamcommunity.com/market/search?appid=730"
        self.required_success_count = 3
        self.test_requests_count = 6
        self.max_workers = 20
        self.timeout = 60
        self._port_counter = 30000
        self._port_lock = threading.Lock()
        
    def test_exit_nodes(self, exit_nodes: List[str]) -> List[str]:
        if not exit_nodes:
            logger.warning("No exit nodes provided for testing")
            return []
            
        logger.info(f"Starting testing of {len(exit_nodes)} exit nodes")
        
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
    
    def _get_unique_test_port(self) -> int:
        with self._port_lock:
            port = self._port_counter
            self._port_counter += 1
            return port
    
    def _test_nodes_parallel(self, exit_nodes: List[str]) -> List[str]:
        working_nodes = []
        lock = threading.Lock()
        total_nodes = len(exit_nodes)
        tested_count = 0
        progress_lock = threading.Lock()
        
        def test_single_node_with_own_process(node_ip: str) -> Optional[str]:
            nonlocal tested_count
            node_instance = None
            try:
                node_port = self._get_unique_test_port()
                node_instance = TorProcess(port=node_port, exit_nodes=[node_ip])
                
                logger.debug(f"Node {node_ip}: Creating dedicated process on port {node_port}")
                
                if not node_instance.create_config(self.config_manager):
                    logger.warning(f"Node {node_ip}: Failed to create config on port {node_port}")
                    return None
                
                if not node_instance.start_process():
                    logger.warning(f"Node {node_ip}: Failed to start process on port {node_port}")
                    return None
                
                if not self._wait_for_startup(node_instance, timeout=60):
                    logger.warning(f"Node {node_ip}: Process failed to start on port {node_port}")
                    return None
                
                logger.debug(f"Node {node_ip}: Process started successfully on port {node_port}")
                
                if self._test_single_node_requests(node_instance, node_ip):
                    logger.info(f"Node {node_ip} PASSED (port {node_port})")
                    result = node_ip
                else:
                    logger.warning(f"Node {node_ip} FAILED (port {node_port})")
                    result = None
                
                with progress_lock:
                    tested_count += 1
                    progress = (tested_count / total_nodes) * 100
                    if tested_count % 10 == 0 or tested_count == total_nodes:
                        logger.info(f"Testing progress: {tested_count}/{total_nodes} nodes tested ({progress:.1f}%)")
                
                return result
                
            except Exception as e:
                logger.error(f"Error testing node {node_ip}: {e}")
                with progress_lock:
                    tested_count += 1
                return None
            finally:
                if node_instance:
                    node_instance.stop_process()
                    node_instance.cleanup()
                    logger.debug(f"Node {node_ip}: Cleaned up process on port {node_instance.port}")
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_node = {
                executor.submit(test_single_node_with_own_process, node): node 
                for node in exit_nodes
            }
            
            for future in as_completed(future_to_node):
                node = future_to_node[future]
                try:
                    result = future.result()
                    if result:
                        with lock:
                            working_nodes.append(result)
                except Exception as e:
                    logger.error(f"Exception occurred while testing node {node}: {e}")
        
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