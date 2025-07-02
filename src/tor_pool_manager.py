import asyncio
import logging
import threading
import time
from typing import Dict, List, Set
from datetime import datetime, timedelta
from utils import safe_stop_thread
from collections import defaultdict

from tor_instance_manager import TorInstanceManager
from balancer_diagnostics import BalancerDiagnostics


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
        
        
        self.stats = {
            'total_instances': 0,
            'running_instances': 0,
            'last_update': None
        }
        
        self.global_exit_node_stats: Dict[str, dict] = {}
        self.global_suspicious_nodes: Set[str] = set()
        self.global_blacklisted_nodes: Set[str] = set()
        
        self.diagnostics = BalancerDiagnostics(self, load_balancer)
        
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
            
            
            safe_stop_thread(self._cleanup_thread)
                
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
            base_stats = self.stats.copy()
            exit_stats = self.get_exit_node_global_stats()
            base_stats['exit_node_monitoring'] = exit_stats['global_totals']
            return base_stats

    def get_instance_statuses(self) -> List[dict]:
        with self._lock:
            return [instance.get_status() for instance in self.instances.values()]
            
    def _create_instance(self, port: int, exit_nodes: List[str], add_to_pool: bool = False) -> bool:
        try:
            logger.info(f"Creating Tor instance on port {port} with {len(exit_nodes)} exit nodes")
            instance = TorInstanceManager(
                port=port,
                exit_nodes=exit_nodes,
                config_manager=self.config_manager
            )
            
            logger.info(f"Starting Tor instance on port {port}...")
            if instance.start():
                logger.info(f"Tor instance started successfully on port {port}")
                
                if add_to_pool:
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
                    if instance.is_running:
                        if port not in self.added_to_balancer:
                            try:
                                is_healthy = instance.is_healthy()
                                logger.debug(f"Instance {port}: running=True, healthy={is_healthy}, failed_checks={instance.failed_checks}")
                                
                                if is_healthy:
                                    logger.info(f"Adding healthy instance {port} to load balancer...")
                                    self.load_balancer.add_proxy(port)
                                    self.added_to_balancer.add(port)
                                    healthy_count += 1
                                else:
                                    if instance.failed_checks >= 10:
                                        logger.warning(f"Instance {port} has {instance.failed_checks} failed checks but is running - adding anyway")
                                        self.load_balancer.add_proxy(port)
                                        self.added_to_balancer.add(port)
                                        healthy_count += 1
                                    else:
                                        logger.debug(f"Instance {port} not healthy yet (failed_checks={instance.failed_checks}), will retry")
                            except Exception as e:
                                logger.error(f"Failed to add {port} to balancer: {e}")
                        else:
                            logger.debug(f"Instance {port} already in load balancer")
                    else:
                        logger.warning(f"Instance {port} not running")
                        if port in self.added_to_balancer:
                            self._remove_from_load_balancer(port)
                        
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
        balancer_check_counter = 0
        aggressive_add_counter = 0
        exit_node_monitor_counter = 0
        
        while not self._shutdown_event.is_set() and self.running:
            try:
                self._check_dead_instances()
                self._update_stats()
                
                balancer_check_counter += 1
                if balancer_check_counter >= 1:
                    self._update_load_balancer()
                    balancer_check_counter = 0
                
                aggressive_add_counter += 1
                if aggressive_add_counter >= 3:
                    self._aggressive_add_running_instances()
                    aggressive_add_counter = 0
                
                exit_node_monitor_counter += 1
                if exit_node_monitor_counter >= 2:
                    self._update_global_exit_node_monitoring()
                    exit_node_monitor_counter = 0
                
                redistribution_counter += 1
                if redistribution_counter >= 5:
                    self.redistribute_nodes()
                    redistribution_counter = 0
                    
                self._shutdown_event.wait(30)
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")
                time.sleep(10)
                
        logger.debug("Stopped cleanup thread")
        
    def _aggressive_add_running_instances(self):
        try:
            with self._lock:
                added_count = 0
                total_running = 0
                
                for port, instance in self.instances.items():
                    if instance.is_running:
                        total_running += 1
                        if port not in self.added_to_balancer:
                            try:
                                logger.info(f"Aggressively adding running instance {port} (failed_checks={instance.failed_checks})")
                                self.load_balancer.add_proxy(port)
                                self.added_to_balancer.add(port)
                                added_count += 1
                            except Exception as e:
                                logger.error(f"Failed to aggressively add {port}: {e}")
                
                if added_count > 0:
                    logger.info(f"Aggressively added {added_count} running instances to balancer")
                    logger.info(f"Status: {total_running} total running, {len(self.added_to_balancer)} in balancer")
                    
        except Exception as e:
            logger.error(f"Error in aggressive add: {e}")
        
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
        logger.info(f"Starting creation of {len(tasks)} instances without global timeout")
        
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Task {i} failed with exception: {result}")
                elif result:
                    completed_count += 1
                    logger.info(f"Task {i} completed successfully ({completed_count}/{len(tasks)})")
                else:
                    logger.warning(f"Task {i} failed to start")
        
        except Exception as e:
            logger.error(f"Error while creating instances: {e}")
            
        logger.info(f"Async instance creation completed: {completed_count}/{len(tasks)} successful")
        
        await asyncio.sleep(2)
        return completed_count
    
    async def _create_instance_async(self, semaphore: asyncio.Semaphore, port: int, exit_nodes: List[str], process_id: int) -> bool:
        async with semaphore:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._create_instance, port, exit_nodes, True
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
    
    def get_exit_node_global_stats(self) -> dict:
        with self._lock:
            all_stats = {}
            total_tracked = 0
            total_active = 0
            total_inactive = 0
            total_suspicious = 0
            total_blacklisted = 0
            
            for instance in self.instances.values():
                stats = instance.get_exit_node_stats()
                all_stats[stats['port']] = stats
                total_tracked += stats['total_tracked_nodes']
                total_active += stats['active_nodes']
                total_inactive += stats['inactive_nodes']
                total_suspicious += stats['suspicious_nodes']
                total_blacklisted += stats['blacklisted_nodes']
            
            return {
                'instances': all_stats,
                'global_totals': {
                    'total_tracked_nodes': total_tracked,
                    'active_nodes': total_active,
                    'inactive_nodes': total_inactive,
                    'suspicious_nodes': total_suspicious,
                    'blacklisted_nodes': total_blacklisted,
                    'global_suspicious_count': len(self.global_suspicious_nodes),
                    'global_blacklisted_count': len(self.global_blacklisted_nodes)
                }
            }

    def blacklist_exit_node_globally(self, ip: str):
        with self._lock:
            self.global_blacklisted_nodes.add(ip)
            self.global_suspicious_nodes.discard(ip)
            
            for instance in self.instances.values():
                instance.blacklist_exit_node(ip)
            
            logger.warning(f"Exit node {ip} blacklisted globally across all instances")

    def get_global_suspicious_nodes(self) -> List[str]:
        with self._lock:
            return list(self.global_suspicious_nodes)

    def get_global_blacklisted_nodes(self) -> List[str]:
        with self._lock:
            return list(self.global_blacklisted_nodes)

    def _update_global_exit_node_monitoring(self):
        with self._lock:
            newly_suspicious = set()
            
            for instance in self.instances.values():
                suspicious = instance.get_suspicious_exit_nodes()
                for node in suspicious:
                    if node not in self.global_suspicious_nodes and node not in self.global_blacklisted_nodes:
                        newly_suspicious.add(node)
                        
            self.global_suspicious_nodes.update(newly_suspicious)
            
            if newly_suspicious:
                logger.warning(f"Added {len(newly_suspicious)} nodes to global suspicious list: {list(newly_suspicious)[:3]}...")

    def redistribute_nodes(self):
        with self._lock:
            if not self.running or not self.instances:
                return
            
            logger.info("Starting exit node redistribution...")
            
            healthy_instances = []
            for port, instance in self.instances.items():
                if instance.is_running:
                    healthy_nodes = instance.get_healthy_exit_nodes()
                    if healthy_nodes:
                        healthy_instances.append((port, instance, healthy_nodes))
            
            if not healthy_instances:
                logger.warning("No healthy instances found for redistribution")
                return
            
            redistributed_count = 0
            for port, instance, healthy_nodes in healthy_instances:
                if len(healthy_nodes) < len(instance.exit_nodes) * 0.5:
                    logger.info(f"Instance {port} has low healthy nodes ratio: {len(healthy_nodes)}/{len(instance.exit_nodes)}")
                    
                    relay_data = self.relay_manager.fetch_tor_relays()
                    if relay_data:
                        new_exit_nodes = self.relay_manager.extract_relay_ips(relay_data)
                        if new_exit_nodes:
                            filtered_nodes = [node for node in new_exit_nodes 
                                            if node not in self.global_blacklisted_nodes 
                                            and node not in self.global_suspicious_nodes]
                            
                            if len(filtered_nodes) > len(instance.exit_nodes):
                                if instance.reload_exit_nodes(filtered_nodes[:len(instance.exit_nodes)]):
                                    redistributed_count += 1
                                    logger.info(f"Redistributed nodes for instance {port}")
            
            if redistributed_count > 0:
                logger.info(f"Completed redistribution for {redistributed_count} instances")
            else:
                logger.debug("No instances needed redistribution")
