import logging
import subprocess
import threading
import concurrent.futures
import socket
from typing import List

logger = logging.getLogger(__name__)


class TorProcessManager:
    def __init__(self, config_manager, load_balancer):
        self.config_manager = config_manager
        self.load_balancer = load_balancer
        self.port_processes = {}
        self.port_exit_nodes = {}
        self._lock = threading.RLock()
        self._next_port = 10000

    def _start_instance(self, exit_nodes: List[str]):
        port = self._get_available_port()

        try:
            tor_config_result = self.config_manager.create_tor_config_by_port(
                port, exit_nodes)
            tor_cmd = ['tor', '-f', tor_config_result['config_path']]

            logger.info(
                f"Starting Tor instance on port {port} with {len(exit_nodes)} exit nodes")

            process = subprocess.Popen(
                tor_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            import time
            time.sleep(5)

            if process.poll() is not None:
                stdout, stderr = process.communicate()
                logger.error(
                    f"Tor process failed to start on port {port}. Exit code: {process.returncode}")
                if stderr:
                    logger.error(f"Tor stderr: {stderr[:1000]}")
                if stdout:
                    logger.error(f"Tor stdout: {stdout[:1000]}")
                return None, None

            with self._lock:
                self.port_processes[port] = process
                self.port_exit_nodes[port] = exit_nodes
                self.load_balancer.add_proxy(port)

            logger.info(f"Started Tor instance on socks5 port {port}")
            return process, port

        except Exception as e:
            logger.error(
                f"Exception starting Tor instance on port {port}: {e}")
            return None, None

    def _get_available_port(self):
        with self._lock:
            port = self._next_port
            self._next_port += 1
            return port

    def _stop_instance(self, port):
        with self._lock:
            if port in self.port_processes:
                self.load_balancer.remove_proxy(port)
                del self.port_processes[port]
                if port in self.port_exit_nodes:
                    del self.port_exit_nodes[port]
                logger.info(
                    f"Removed SOCKS5 proxy port {port} from HTTP load balancer")

    def stop_tor_instance(self, port):
        with self._lock:
            if port in self.port_processes:
                process = self.port_processes[port]
                if process and process.poll() is None:
                    process.terminate()
                self._stop_instance(port)

    def restart_instance_by_port(self, port, exit_nodes: List[str]):
        logger.info(
            f"Restarting Tor instance on port {port} with {len(exit_nodes)} exit nodes")

        logger.info(f"Validating {len(exit_nodes)} exit nodes before restart...")
        validated_nodes = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_ip = {
                executor.submit(self._validate_exit_node, ip): ip 
                for ip in exit_nodes
            }
            
            for future in concurrent.futures.as_completed(future_to_ip):
                ip = future_to_ip[future]
                try:
                    if future.result():
                        validated_nodes.append(ip)
                except Exception as e:
                    logger.debug(f"Validation failed for {ip}: {e}")
        
        if len(validated_nodes) < max(3, len(exit_nodes) // 2):
            logger.error(f"Too few valid nodes for restart: {len(validated_nodes)}/{len(exit_nodes)}")
            return False
        
        logger.info(f"Using {len(validated_nodes)}/{len(exit_nodes)} validated exit nodes")

        with self._lock:
            old_process = self.port_processes.get(port)

            if old_process and old_process.poll() is None:
                old_process.terminate()
            if old_process and old_process.poll() is None:
                old_process.terminate()
                try:
                    old_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    old_process.kill()

            self._stop_instance(port)

            new_process, new_port = self._start_instance(validated_nodes)

            if new_process and new_port:
                logger.info(
                    f"Successfully restarted Tor instance on port {new_port} with {len(validated_nodes)} exit nodes")
                return new_port
            else:
                logger.error(
                    f"Failed to restart Tor instance with {len(validated_nodes)} exit nodes")
                return False

    def count_running_instances(self):
        with self._lock:
            return len([p for p in self.port_processes.values() 
                       if p and p.poll() is None])

    def get_failed_instances(self):
        failed_ports = []

        with self._lock:
            for port, process in self.port_processes.items():
                if not (process and process.poll() is None):
                    exit_nodes_count = len(self.port_exit_nodes.get(port, []))
                    failed_ports.append(f"tor-{port}-{exit_nodes_count}nodes")

        return failed_ports

    def stop_all_instances(self):
        with self._lock:
            for port, process in list(self.port_processes.items()):
                if process and process.poll() is None:
                    process.terminate()
                self._stop_instance(port)

        logger.info("All Tor processes stopped")

    def get_all_ports(self):
        with self._lock:
            return list(self.port_processes.keys())

    def get_port_exit_nodes(self, port):
        with self._lock:
            return self.port_exit_nodes.get(port, [])

    def start_tor_instances_batch(self, exit_nodes_list: List[List[str]], batch_size: int = 10):
        results = []
        total_instances = len(exit_nodes_list)
        logger.info(f"Starting {total_instances} Tor instances in batches of {batch_size}")
        
        logger.info("Validating exit nodes connectivity...")
        validated_exit_nodes_list = self._validate_exit_nodes_batch(exit_nodes_list)
        
        if not validated_exit_nodes_list:
            logger.error("No valid exit nodes found after validation")
            return []
        
        logger.info(f"Validation complete: {len(validated_exit_nodes_list)}/{total_instances} batches have sufficient valid nodes")
        
        for i in range(0, len(validated_exit_nodes_list), batch_size):
            batch = validated_exit_nodes_list[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(validated_exit_nodes_list) + batch_size - 1) // batch_size
            
            logger.info(f"Processing batch {batch_num}/{total_batches} with {len(batch)} instances")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=batch_size) as executor:
                future_to_exit_nodes = {
                    executor.submit(self._start_instance, exit_nodes): exit_nodes 
                    for exit_nodes in batch
                }
                
                batch_results = []
                completed = 0
                for future in concurrent.futures.as_completed(future_to_exit_nodes):
                    exit_nodes = future_to_exit_nodes[future]
                    try:
                        process, port = future.result()
                        if port is not None:
                            batch_results.append({
                                'success': True,
                                'port': port,
                                'exit_nodes': exit_nodes,
                                'process': process
                            })
                            logger.debug(f"Successfully started Tor instance on port {port}")
                        else:
                            batch_results.append({
                                'success': False,
                                'port': None,
                                'exit_nodes': exit_nodes,
                                'process': None
                            })
                            logger.warning(f"Failed to start Tor instance with {len(exit_nodes)} exit nodes")
                    except Exception as e:
                        logger.error(f"Exception in batch start for exit nodes {len(exit_nodes)}: {e}")
                        batch_results.append({
                            'success': False,
                            'port': None,
                            'exit_nodes': exit_nodes,
                            'process': None
                        })
                    
                    completed += 1
                
                results.extend(batch_results)
                successful_in_batch = sum(1 for r in batch_results if r['success'])
                logger.info(f"Batch {batch_num}/{total_batches} completed: {successful_in_batch}/{len(batch)} instances started successfully")
        
        total_successful = sum(1 for r in results if r['success'])
        logger.info(f"All batches completed: {total_successful}/{len(validated_exit_nodes_list)} instances started successfully")
        return results

    def _verify_tor_exit_node(self, ip_address, timeout=10):
        try:
            import requests
            url = f"https://check.torproject.org/torbulkexitlist?ip={ip_address}"
            response = requests.get(url, timeout=timeout)
            return response.status_code == 200 and ip_address in response.text
        except Exception:
            return True

    def _validate_exit_node(self, ip_address, port=9001, timeout=5):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            result = sock.connect_ex((ip_address, port))
            sock.close()
            
            if result == 0:
                return self._verify_tor_exit_node(ip_address)
            return False
        except Exception:
            return False

    def _validate_exit_nodes_batch(self, exit_nodes_list, max_workers=20):
        validated_list = []
        
        for exit_nodes in exit_nodes_list:
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_ip = {
                    executor.submit(self._validate_exit_node, ip): ip 
                    for ip in exit_nodes
                }
                
                valid_nodes = []
                for future in concurrent.futures.as_completed(future_to_ip):
                    ip = future_to_ip[future]
                    try:
                        if future.result():
                            valid_nodes.append(ip)
                    except Exception as e:
                        logger.debug(f"Validation failed for {ip}: {e}")
                
                if len(valid_nodes) >= max(3, len(exit_nodes) // 2):
                    validated_list.append(valid_nodes)
                    logger.info(f"Validated {len(valid_nodes)}/{len(exit_nodes)} exit nodes")
                else:
                    logger.warning(f"Too few valid nodes: {len(valid_nodes)}/{len(exit_nodes)}, skipping batch")
        
        return validated_list
