import logging
import threading
import time
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import queue

from tor_instance_manager import TorInstanceManager
from exit_node_monitor import ExitNodeMonitor, NodeRedistributor


logger = logging.getLogger(__name__)


class TorPoolManager:
    def __init__(self, config_manager, load_balancer, relay_manager):
        self.config_manager = config_manager
        self.load_balancer = load_balancer
        self.relay_manager = relay_manager
        
        self.instances: Dict[int, TorInstanceManager] = {}
        self.added_to_balancer: set = set()
        self.completion_queue = queue.Queue()
        self.next_port = 10000
        self.running = False
        
        self._lock = threading.RLock()
        self._cleanup_thread = None
        self._shutdown_event = threading.Event()
        
        self.exit_node_monitor = ExitNodeMonitor()
        self.node_redistributor = NodeRedistributor(
            self.exit_node_monitor, 
            self, 
            self.relay_manager
        )
        
        self.stats = {
            'total_instances': 0,
            'running_instances': 0,
            'last_update': None
        }
        
    def start(self, instance_count: int, batch_size: int = 10) -> bool:
        with self._lock:
            if self.running:
                return True
                
            relay_data = self.relay_manager.fetch_tor_relays()
            if not relay_data:
                logger.error("Failed to fetch Tor relay data")
                return False
                
            exit_nodes = self.relay_manager.extract_relay_ips(relay_data)
            if not exit_nodes:
                logger.error("No exit nodes extracted from relay data")
                return False
            
            logger.info(f"Found {len(exit_nodes)} exit nodes")
                
            node_distributions = self.relay_manager.distribute_exit_nodes(instance_count)
            if not node_distributions:
                logger.error("Failed to distribute exit nodes across instances")
                return False
            
            processes_with_nodes = sum(1 for d in node_distributions.values() if d.get('exit_nodes'))
            logger.info(f"Created node distributions for {processes_with_nodes}/{instance_count} processes")
                
            success_count = 0
            logger.info(f"Starting creation of {instance_count} Tor instances in batches of {batch_size}...")
            logger.info(f"Total batches to process: {(instance_count + batch_size - 1) // batch_size}")
            
            for batch_start in range(0, instance_count, batch_size):
                batch_end = min(batch_start + batch_size, instance_count)
                batch_num = batch_start // batch_size + 1
                total_batches = (instance_count + batch_size - 1) // batch_size
                
                logger.info(f"Starting batch {batch_num}/{total_batches}: instances {batch_start + 1}-{batch_end}")
                
                # Используем ThreadPoolExecutor для параллельного запуска инстансов в батче
                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    futures = []
                    batch_ports = []
                    for process_id in range(batch_start, batch_end):
                        if process_id in node_distributions:
                            process_exit_nodes = node_distributions[process_id]['exit_nodes']
                            if process_exit_nodes:
                                port = self._get_next_port()
                                batch_ports.append(port)
                                future = executor.submit(self._create_instance, port, process_exit_nodes)
                                futures.append((future, process_id, port))
                            else:
                                logger.warning(f"Process {process_id} has no exit nodes assigned")
                        else:
                            logger.warning(f"Process {process_id} not found in node distributions")
                    
                    logger.info(f"Submitted {len(futures)} tasks for batch {batch_num} (ports: {batch_ports})")
                    
                    logger.info(f"Waiting for batch {batch_num} instances to be created...")
                    
                    start_wait = time.time()
                    max_wait = 60
                    
                    while time.time() - start_wait < max_wait:
                        # Process completed instances from the queue
                        while True:
                            try:
                                port, instance = self.completion_queue.get_nowait()
                                with self._lock:
                                    self.instances[port] = instance
                                    logger.info(f"Added instance {port} to dict from queue, total instances: {len(self.instances)}")
                            except queue.Empty:
                                break
                        
                        # Give worker threads a chance to acquire the lock
                        time.sleep(0.5)
                        
                        with self._lock:
                            current_count = sum(1 for port in batch_ports if port in self.instances)
                            total_instances = len(self.instances)
                            instance_keys = list(self.instances.keys())
                        
                        logger.debug(f"Batch {batch_num}: checking {len(batch_ports)} ports: {batch_ports}")
                        logger.debug(f"Batch {batch_num}: instances dict has {total_instances} total instances: {instance_keys}")
                        logger.debug(f"Batch {batch_num}: found {current_count} matching instances")
                        
                        if current_count >= len(batch_ports):
                            logger.info(f"All {current_count} instances in batch {batch_num} have been created")
                            break
                        
                        elapsed = time.time() - start_wait
                        logger.debug(f"Batch {batch_num}: {current_count}/{len(batch_ports)} instances created after {elapsed:.1f}s")
                        time.sleep(1.5)  # Longer sleep to allow worker threads to work
                    
                    with self._lock:
                        completed_count = sum(1 for port in batch_ports if port in self.instances)
                    
                    logger.info(f"Batch {batch_num}/{total_batches} started: {completed_count}/{len(futures)} instances")
                    
                    logger.info(f"Checking readiness of batch {batch_num} instances...")
                    ready_count = 0
                    max_wait_time = 60
                    start_time = time.time()
                    check_count = 0
                    failed_ports = set()
                    
                    while ready_count < completed_count and (time.time() - start_time) < max_wait_time:
                        ready_count = 0
                        check_count += 1
                        
                        with self._lock:
                            for port in batch_ports:
                                if port in failed_ports:
                                    continue
                                    
                                if port in self.instances:
                                    instance = self.instances[port]
                                    if instance.is_running and instance.is_healthy():
                                        ready_count += 1
                                        self._add_to_load_balancer(port)
                                        logger.debug(f"Port {port} is healthy and ready, added to load balancer")
                                    elif not instance.is_running or (instance.process and instance.process.poll() is not None):
                                        failed_ports.add(port)
                                        logger.warning(f"Instance on port {port} failed to start properly, marking for removal")
                                    else:
                                        logger.debug(f"Port {port} is running but not healthy yet")
                                else:
                                    if (time.time() - start_time) > 30:
                                        failed_ports.add(port)
                                        logger.warning(f"Instance on port {port} was never created, marking as failed")
                        
                        elapsed = time.time() - start_time
                        active_instances = completed_count - len(failed_ports)
                        logger.info(f"Batch {batch_num} check #{check_count}: {ready_count}/{active_instances} proxies responding after {elapsed:.1f}s")
                        
                        if ready_count < active_instances and elapsed < max_wait_time:
                            time.sleep(5)
                        else:
                            break
                    
                    for failed_port in failed_ports:
                        with self._lock:
                            if failed_port in self.instances:
                                logger.warning(f"Stopping failed instance on port {failed_port}")
                                instance = self.instances[failed_port]
                                instance.stop()
                                del self.instances[failed_port]
                                self._remove_from_load_balancer(failed_port)
                    
                    final_ready = ready_count
                    final_active = completed_count - len(failed_ports)
                    if failed_ports:
                        logger.warning(f"Batch {batch_num}/{total_batches}: {len(failed_ports)} instances failed and were stopped")
                    logger.info(f"Batch {batch_num}/{total_batches} readiness check completed: {final_ready}/{final_active} proxies working")
                
                if batch_num < total_batches:
                    logger.info(f"Waiting before next batch...")
                    time.sleep(2)
                            
            with self._lock:
                final_count = len(self.instances)
            logger.info(f"Created {final_count} out of {instance_count} requested Tor instances")
            
            if final_count == 0:
                return False
                
            self.running = True
            self.exit_node_monitor.start_monitoring()
            self._start_cleanup_thread()
            self._update_load_balancer()
            self._update_stats()
            
            logger.info(f"Pool started with {final_count} instances, updating load balancer...")
            return True
            
    def stop(self):
        with self._lock:
            if not self.running:
                return
                
            self.running = False
            self._shutdown_event.set()
            
            self.exit_node_monitor.stop_monitoring()
            
            if self._cleanup_thread and self._cleanup_thread.is_alive():
                self._cleanup_thread.join(timeout=10)
                
            instances_to_stop = list(self.instances.values())
            
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures = [executor.submit(instance.stop) for instance in instances_to_stop]
                
                for future in as_completed(futures, timeout=30):
                    try:
                        future.result()
                    except Exception:
                        pass
                        
            self.instances.clear()
            self._update_stats()
            
    def get_stats(self) -> dict:
        with self._lock:
            basic_stats = self.stats.copy()
            monitor_stats = self.exit_node_monitor.get_stats()
            basic_stats.update({
                'exit_node_monitoring': monitor_stats
            })
            return basic_stats
            
    def redistribute_nodes(self) -> bool:
        return self.node_redistributor.redistribute_nodes()
        
    def refresh_backup_nodes(self) -> bool:
        return self.node_redistributor.refresh_backup_nodes()
        
    def get_instance_statuses(self) -> List[dict]:
        with self._lock:
            return [instance.get_status() for instance in self.instances.values()]
            
    def _create_instance(self, port: int, exit_nodes: List[str]) -> bool:
        try:
            logger.info(f"Creating Tor instance on port {port} with {len(exit_nodes)} exit nodes")
            instance = TorInstanceManager(
                port=port,
                exit_nodes=exit_nodes,
                config_manager=self.config_manager,
                exit_node_monitor=self.exit_node_monitor
            )
            
            logger.info(f"Starting Tor instance on port {port}...")
            if instance.start():
                logger.info(f"Tor instance on port {port} started, adding to instances dict...")
                self.completion_queue.put((port, instance))
                logger.info(f"Tor instance on port {port} queued for addition to instances dict")
                    
                logger.info(f"Tor instance on port {port} started successfully")
                return True
            else:
                logger.error(f"Failed to start Tor instance on port {port}")
                return False
                
        except Exception as e:
            logger.error(f"Exception creating Tor instance on port {port}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
            
    def _remove_instance(self, port: int):
        with self._lock:
            if port in self.instances:
                instance = self.instances[port]
                instance.stop()
                del self.instances[port]
                
        self._remove_from_load_balancer(port)
        
    def _get_next_port(self) -> int:
        with self._lock:
            while self.next_port in self.instances:
                self.next_port += 1
            port = self.next_port
            self.next_port += 1
            return port
            
    def _add_to_load_balancer(self, port: int):
        try:
            if port not in self.added_to_balancer:
                logger.info(f"Adding port {port} to load balancer...")
                self.load_balancer.add_proxy(port)
                self.added_to_balancer.add(port)
                logger.info(f"Successfully added port {port} to load balancer")
            else:
                logger.debug(f"Port {port} already added to load balancer")
        except Exception as e:
            logger.error(f"Failed to add port {port} to load balancer: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            
    def _remove_from_load_balancer(self, port: int):
        try:
            if port in self.added_to_balancer:
                self.load_balancer.remove_proxy(port)
                self.added_to_balancer.remove(port)
                logger.info(f"Removed port {port} from load balancer")
        except Exception as e:
            logger.error(f"Failed to remove port {port} from load balancer: {e}")
            
    def _update_load_balancer(self):
        try:
            with self._lock:
                healthy_count = 0
                for port, instance in self.instances.items():
                    if instance.is_running and instance.is_healthy():
                        if port not in self.added_to_balancer:
                            self.load_balancer.add_proxy(port)
                            self.added_to_balancer.add(port)
                            healthy_count += 1
                logger.info(f"Updated load balancer with {healthy_count} new healthy instances")
        except Exception as e:
            logger.error(f"Failed to update load balancer: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
        
    def _start_cleanup_thread(self):
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            return
            
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_loop,
            name="TorPoolCleanup"
        )
        self._cleanup_thread.daemon = True
        self._cleanup_thread.start()
        
    def _cleanup_loop(self):
        logger.debug("Started cleanup thread")
        
        redistribution_counter = 0
        
        while not self._shutdown_event.is_set() and self.running:
            try:
                self._check_dead_instances()
                self._update_stats()
                
                redistribution_counter += 1
                if redistribution_counter >= 5:
                    self.redistribute_nodes()
                    redistribution_counter = 0
                    
                self._shutdown_event.wait(60)
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                time.sleep(10)
                
        logger.debug("Stopped cleanup thread")
        
    def _check_dead_instances(self):
        dead_instances = []
        
        with self._lock:
            for port, instance in self.instances.items():
                if not instance.is_running or (instance.process and instance.process.poll() is not None):
                    dead_instances.append(port)
                    
        for port in dead_instances:
            logger.warning(f"Found dead instance on port {port}, removing")
            self._remove_instance(port)
            
        if dead_instances:
            self._update_load_balancer()
            
    def _update_stats(self):
        with self._lock:
            self.stats['total_instances'] = len(self.instances)
            self.stats['running_instances'] = sum(1 for instance in self.instances.values() if instance.is_running)
            self.stats['last_update'] = datetime.now()
