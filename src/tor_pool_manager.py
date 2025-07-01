import asyncio
import logging
import threading
import time
import psutil
from typing import Dict, List
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
        self.added_to_balancer: set = set()
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
        
    def start(self, instance_count: int, max_concurrent: int = 5) -> bool:
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
            logger.info(f"Starting parallel creation of {instance_count} Tor instances with max {max_concurrent} concurrent tasks...")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            completed_count = loop.run_until_complete(
                self._create_instances_async(instance_count, node_distributions, max_concurrent)
            )
        finally:
            loop.close()
        with self._lock:
            final_count = len(self.instances)
            balancer_count = len(self.added_to_balancer)
        logger.info(f"Created {final_count} out of {instance_count} requested Tor instances")
        logger.info(f"Load balancer has {balancer_count} proxies: {list(self.added_to_balancer)}")
        if final_count == 0:
            return False
        with self._lock:
            self.running = True
        self.exit_node_monitor.start_monitoring()
        self._start_cleanup_thread()
        self._update_stats()
        logger.info(f"Pool started successfully with {final_count} instances, {balancer_count} in load balancer")
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
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(self._stop_instances_async(instances_to_stop))
            finally:
                loop.close()
                        
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
            
    def _create_instance_with_wait(self, port: int, exit_nodes: List[str]) -> bool:
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
                logger.info(f"Tor instance on port {port} started successfully")
                
                with self._lock:
                    self.instances[port] = instance
                
                logger.info(f"Adding port {port} to load balancer...")
                self._add_to_load_balancer(port)
                return True
            else:
                logger.error(f"Failed to start Tor instance on port {port}")
                return False
                
        except Exception as e:
            logger.error(f"Exception creating Tor instance on port {port}: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return False
            
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
                logger.info(f"Tor instance started successfully on port {port}")
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
                total_instances = len(self.instances)
                logger.info(f"Updating load balancer with {total_instances} total instances...")
                
                for port, instance in self.instances.items():
                    logger.debug(f"Checking instance {port}: running={instance.is_running}, healthy={instance.is_healthy()}")
                    if instance.is_running and instance.is_healthy():
                        if port not in self.added_to_balancer:
                            logger.info(f"Adding healthy instance {port} to load balancer...")
                            self.load_balancer.add_proxy(port)
                            self.added_to_balancer.add(port)
                            healthy_count += 1
                        else:
                            logger.debug(f"Instance {port} already in load balancer")
                    else:
                        logger.warning(f"Instance {port} not healthy: running={instance.is_running}, healthy={instance.is_healthy()}")
                        
                logger.info(f"Load balancer update completed: {healthy_count} new instances added, {len(self.added_to_balancer)} total in balancer")
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
            
    async def _create_instances_async(self, instance_count: int, node_distributions: dict, max_concurrent: int) -> int:
        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = []
        all_ports = []
        
        for process_id in range(instance_count):
            if process_id in node_distributions:
                process_exit_nodes = node_distributions[process_id]['exit_nodes']
                if process_exit_nodes:
                    port = self._get_next_port()
                    all_ports.append(port)
                    task = self._create_instance_async(semaphore, port, process_exit_nodes, process_id)
                    tasks.append(task)
                else:
                    logger.warning(f"Process {process_id} has no exit nodes assigned")
            else:
                logger.warning(f"Process {process_id} not found in node distributions")
        
        logger.info(f"Created {len(tasks)} async tasks for parallel execution (ports: {all_ports})")
        
        if not tasks:
            return 0
        
        completed_count = 0
        timeout_duration = min(600, max(120, instance_count * 3))
        logger.info(f"Using timeout of {timeout_duration} seconds for {instance_count} instances")
        
        try:
            results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout_duration)
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Task {i} failed with exception: {result}")
                elif result:
                    completed_count += 1
                    logger.info(f"Task {i} completed successfully ({completed_count}/{len(tasks)})")
                else:
                    logger.warning(f"Task {i} failed to start")
        
        except asyncio.TimeoutError:
            logger.error(f"Timeout while creating instances after {timeout_duration} seconds")
            
        logger.info(f"Async instance creation completed: {completed_count}/{len(tasks)} successful")
        
        await asyncio.sleep(2)
        return completed_count
    
    async def _create_instance_async(self, semaphore: asyncio.Semaphore, port: int, exit_nodes: List[str], process_id: int) -> bool:
        async with semaphore:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._create_instance_with_wait, port, exit_nodes
            )
        
    async def _stop_instances_async(self, instances_to_stop: List[TorInstanceManager]):
        if not instances_to_stop:
            return
            
        tasks = []
        for instance in instances_to_stop:
            task = asyncio.get_event_loop().run_in_executor(None, instance.stop)
            tasks.append(task)
        
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Timeout while stopping instances")
