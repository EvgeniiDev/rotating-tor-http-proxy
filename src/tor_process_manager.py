import logging
import subprocess
import threading
import concurrent.futures
import requests
from typing import List, Set

logger = logging.getLogger(__name__)


class TorProcessManager:
    def __init__(self, config_manager, load_balancer):
        self.config_manager = config_manager
        self.load_balancer = load_balancer
        self.port_processes = {}
        self.port_exit_nodes = {}
        self._lock = threading.RLock()
        self._next_port = 10000
        self._tor_exit_nodes: Set[str] = set()
        self._exit_nodes_loaded = False

    def _load_tor_exit_nodes(self, timeout=30):
        if self._exit_nodes_loaded:
            return True
        
        try:
            logger.info("Loading Tor exit nodes list...")
            response = requests.get("https://check.torproject.org/torbulkexitlist", timeout=timeout)
            if response.status_code == 200:
                exit_nodes = set()
                for line in response.text.strip().split('\n'):
                    ip = line.strip()
                    if ip:
                        exit_nodes.add(ip)
                
                self._tor_exit_nodes = exit_nodes
                self._exit_nodes_loaded = True
                logger.info(f"Loaded {len(exit_nodes)} Tor exit nodes")
                return True
            else:
                logger.error(f"Failed to load exit nodes: HTTP {response.status_code}")
                return False
        except Exception as e:
            logger.error(f"Error loading Tor exit nodes: {e}")
            return False

    def _is_tor_exit_node(self, ip_address):
        if not self._exit_nodes_loaded:
            if not self._load_tor_exit_nodes():
                return False
        
        return ip_address in self._tor_exit_nodes

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
            startup_timeout = 15
            check_interval = 1
            elapsed = 0
            
            while elapsed < startup_timeout:
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    logger.error(
                        f"Tor process failed to start on port {port}. Exit code: {process.returncode}")
                    if stderr:
                        logger.error(f"Tor stderr: {stderr[:1000]}")
                    if stdout:
                        logger.error(f"Tor stdout: {stdout[:1000]}")
                    return None, None
                
                time.sleep(check_interval)
                elapsed += check_interval
                
                if elapsed >= 8:
                    break

            if process.poll() is not None:
                stdout, stderr = process.communicate()
                logger.error(
                    f"Tor process died during startup on port {port}. Exit code: {process.returncode}")
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

        logger.info(f"Prioritizing {len(exit_nodes)} exit nodes before restart...")
        min_required = max(3, len(exit_nodes) // 2)
        validated_nodes = self._validate_and_prioritize_exit_nodes(exit_nodes, min_required)
        
        official_count = len([ip for ip in validated_nodes if self._is_tor_exit_node(ip)])
        unofficial_count = len(validated_nodes) - official_count
        
        if len(validated_nodes) < min_required:
            logger.error(f"Too few nodes available for restart: {len(validated_nodes)}/{min_required} required")
            return False
        
        logger.info(f"Using {official_count} official + {unofficial_count} unofficial = {len(validated_nodes)} total nodes")

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

    def stop_all_instances(self):
        with self._lock:
            for port, process in list(self.port_processes.items()):
                if process and process.poll() is None:
                    process.terminate()
                self._stop_instance(port)

        logger.info("All Tor processes stopped")

    def get_port_exit_nodes(self, port):
        with self._lock:
            return self.port_exit_nodes.get(port, [])

    def start_tor_instances_batch(self, exit_nodes_list: List[List[str]], batch_size: int = 3):
        results = []
        total_instances = len(exit_nodes_list)
        logger.info(f"Starting {total_instances} Tor instances sequentially in batches of {batch_size}")
        
        logger.info("Distributing exit nodes evenly across all processes...")
        distributed_exit_nodes_list = self._distribute_nodes_evenly(exit_nodes_list)
        
        if not distributed_exit_nodes_list:
            logger.error("No sufficient exit nodes found after distribution")
            return []
        
        logger.info(f"Distribution complete: {len(distributed_exit_nodes_list)}/{total_instances} processes will be started")
        
        for i in range(0, len(distributed_exit_nodes_list), batch_size):
            batch = distributed_exit_nodes_list[i:i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(distributed_exit_nodes_list) + batch_size - 1) // batch_size
            
            logger.info(f"Processing batch {batch_num}/{total_batches} with {len(batch)} instances")
            
            batch_results = []
            for j, exit_nodes in enumerate(batch):
                instance_num = j + 1
                logger.info(f"Starting instance {instance_num}/{len(batch)} in batch {batch_num}")
                
                try:
                    process, port = self._start_instance(exit_nodes)
                    if port is not None:
                        batch_results.append({
                            'success': True,
                            'port': port,
                            'exit_nodes': exit_nodes,
                            'process': process
                        })
                        logger.info(f"Successfully started Tor instance on port {port}")
                    else:
                        batch_results.append({
                            'success': False,
                            'port': None,
                            'exit_nodes': exit_nodes,
                            'process': None
                        })
                        logger.warning(f"Failed to start Tor instance with {len(exit_nodes)} exit nodes")
                except Exception as e:
                    logger.error(f"Exception starting Tor instance with {len(exit_nodes)} exit nodes: {e}")
                    batch_results.append({
                        'success': False,
                        'port': None,
                        'exit_nodes': exit_nodes,
                        'process': None
                    })
                
                import time
                if j < len(batch) - 1:
                    time.sleep(3)
            
            results.extend(batch_results)
            successful_in_batch = sum(1 for r in batch_results if r['success'])
            logger.info(f"Batch {batch_num}/{total_batches} completed: {successful_in_batch}/{len(batch)} instances started successfully")
            
            if batch_num < total_batches:
                import time
                time.sleep(2)
        
        total_successful = sum(1 for r in results if r['success'])
        logger.info(f"All batches completed: {total_successful}/{len(distributed_exit_nodes_list)} instances started successfully")
        return results

    def _validate_and_prioritize_exit_nodes(self, exit_nodes: List[str], min_required: int = 3):
        if not self._load_tor_exit_nodes():
            logger.warning("Failed to load official Tor exit nodes list, using all provided nodes")
            return exit_nodes
        
        official_nodes = [ip for ip in exit_nodes if self._is_tor_exit_node(ip)]
        unofficial_nodes = [ip for ip in exit_nodes if not self._is_tor_exit_node(ip)]
        
        prioritized_nodes = official_nodes.copy()
        prioritized_nodes.extend(unofficial_nodes)
        
        if len(official_nodes) == 0:
            logger.warning(f"No official nodes found, using {len(unofficial_nodes)} unofficial nodes")
        elif len(unofficial_nodes) == 0:
            logger.info(f"Using {len(official_nodes)} official nodes only")
        else:
            logger.info(f"Using {len(official_nodes)} official + {len(unofficial_nodes)} unofficial nodes")
        
        return prioritized_nodes

    def _distribute_nodes_evenly(self, exit_nodes_list: List[List[str]]):
        if not exit_nodes_list:
            return []
        
        if not self._load_tor_exit_nodes():
            logger.warning("Failed to load official Tor exit nodes list")
        
        all_nodes = set()
        for nodes in exit_nodes_list:
            all_nodes.update(nodes)
        
        all_nodes = list(all_nodes)
        official_nodes = [ip for ip in all_nodes if self._is_tor_exit_node(ip)]
        unofficial_nodes = [ip for ip in all_nodes if not self._is_tor_exit_node(ip)]
        
        num_processes = len(exit_nodes_list)
        total_nodes = len(all_nodes)
        base_nodes_per_process = total_nodes // num_processes
        extra_nodes = total_nodes % num_processes
        min_nodes_per_process = max(3, base_nodes_per_process)
        
        logger.info(f"Distributing {len(official_nodes)} official + {len(unofficial_nodes)} unofficial nodes among {num_processes} processes")
        logger.info(f"Target: {base_nodes_per_process}-{base_nodes_per_process + 1} nodes per process (min {min_nodes_per_process})")
        
        if base_nodes_per_process < min_nodes_per_process:
            logger.warning(f"Not enough nodes for even distribution: {total_nodes} total / {num_processes} processes = {base_nodes_per_process} < {min_nodes_per_process} minimum")
        
        distributed_lists = []
        all_available_nodes = official_nodes + unofficial_nodes
        
        for i in range(num_processes):
            nodes_for_this_process = base_nodes_per_process + (1 if i < extra_nodes else 0)
            
            start_idx = i * base_nodes_per_process + min(i, extra_nodes)
            end_idx = start_idx + nodes_for_this_process
            
            process_nodes = all_available_nodes[start_idx:end_idx]
            
            if len(process_nodes) >= min_nodes_per_process:
                distributed_lists.append(process_nodes)
                official_count = len([ip for ip in process_nodes if self._is_tor_exit_node(ip)])
                unofficial_count = len(process_nodes) - official_count
                logger.info(f"Process {i+1}: {official_count} official + {unofficial_count} unofficial = {len(process_nodes)} total nodes")
            else:
                logger.warning(f"Process {i+1}: insufficient nodes ({len(process_nodes)}/{min_nodes_per_process}), skipping")
        
        return distributed_lists
