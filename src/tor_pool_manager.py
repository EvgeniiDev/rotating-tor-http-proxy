import logging
import threading
import time
from typing import Dict, List
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tor_instance_manager import TorInstanceManager
from exit_node_monitor import ExitNodeMonitor, NodeRedistributor


logger = logging.getLogger(__name__)


class TorPoolManager:
    def __init__(self, config_manager, load_balancer, relay_manager):
        self.config_manager = config_manager
        self.load_balancer = load_balancer
        self.relay_manager = relay_manager
        
        self.instances: Dict[int, TorInstanceManager] = {}
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
        
    def start(self, instance_count: int) -> bool:
        with self._lock:
            if self.running:
                return True
                
            relay_data = self.relay_manager.fetch_tor_relays()
            if not relay_data:
                return False
                
            exit_nodes = self.relay_manager.extract_relay_ips(relay_data)
            if not exit_nodes:
                return False
                
            node_distributions = self.relay_manager.distribute_exit_nodes(instance_count)
            if not node_distributions:
                return False
                
            success_count = 0
            batch_size = 10
            
            logger.info(f"Starting creation of {instance_count} Tor instances in batches of {batch_size}...")
            
            for batch_start in range(0, instance_count, batch_size):
                batch_end = min(batch_start + batch_size, instance_count)
                
                logger.info(f"Starting batch {batch_start // batch_size + 1}: instances {batch_start + 1}-{batch_end}")
                
                # Используем ThreadPoolExecutor для параллельного запуска инстансов в батче
                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    futures = []
                    for process_id in range(batch_start, batch_end):
                        if process_id in node_distributions:
                            process_exit_nodes = node_distributions[process_id]['exit_nodes']
                            if process_exit_nodes:
                                port = self._get_next_port()
                                future = executor.submit(self._create_instance, port, process_exit_nodes)
                                futures.append((future, process_id, port))
                    
                    logger.info(f"Submitted {len(futures)} tasks for batch {batch_start // batch_size + 1}")
                    
                    # Даем время на создание инстансов, не ждем future.result()
                    time.sleep(2)
                
                # Обновляем счетчик по фактическому состоянию
                with self._lock:
                    success_count = len(self.instances)
                
                logger.info(f"Batch {batch_start // batch_size + 1} completed, total instances: {success_count}")
                
                logger.info(f"Completed batch {batch_start // batch_size + 1}: {success_count} total instances created so far")
                            
            logger.info(f"Created {success_count} out of {instance_count} requested Tor instances")
            
            if success_count == 0:
                return False
                
            self.running = True
            self.exit_node_monitor.start_monitoring()
            self._start_cleanup_thread()
            self._update_load_balancer()
            self._update_stats()
            
            logger.info(f"Pool started with {success_count} instances, updating load balancer...")
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
            logger.debug(f"Creating Tor instance on port {port} with {len(exit_nodes)} exit nodes")
            instance = TorInstanceManager(
                port=port,
                exit_nodes=exit_nodes,
                config_manager=self.config_manager,
                exit_node_monitor=self.exit_node_monitor
            )
            
            logger.debug(f"Starting Tor instance on port {port}...")
            if instance.start():
                logger.debug(f"Tor instance on port {port} started, adding to instances dict...")
                with self._lock:
                    self.instances[port] = instance
                    logger.debug(f"Added instance {port} to dict, total instances: {len(self.instances)}")
                    
                self._add_to_load_balancer(port)
                logger.info(f"Tor instance on port {port} started and added to load balancer")
                return True
            else:
                logger.warning(f"Failed to start Tor instance on port {port}")
                return False
                
        except Exception as e:
            logger.error(f"Exception creating Tor instance on port {port}: {e}")
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
            self.load_balancer.add_proxy(port)
        except Exception:
            pass
            
    def _remove_from_load_balancer(self, port: int):
        try:
            self.load_balancer.remove_proxy(port)
        except Exception:
            pass
            
    def _update_load_balancer(self):
        try:
            with self._lock:
                for port in self.instances.keys():
                    self.load_balancer.add_proxy(port)
        except Exception:
            pass
        
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
