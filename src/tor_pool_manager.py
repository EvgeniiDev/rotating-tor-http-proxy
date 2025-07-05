import asyncio
import logging
import threading
import time
from typing import Dict, List, Set, Generator, Optional
from datetime import datetime
from utils import safe_stop_thread

from tor_process import TorProcess

logger = logging.getLogger(__name__)


class TorPoolManager:
    __slots__ = (
        'config_manager', 'load_balancer', 'relay_manager', 'instances',
        'added_to_balancer', 'next_port', 'running', '_lock', '_cleanup_thread',
        '_shutdown_event', 'stats', 'global_suspicious_nodes', 
        'global_blacklisted_nodes'
    )
    
    def __init__(self, config_manager, load_balancer, relay_manager):
        self.config_manager = config_manager
        self.load_balancer = load_balancer
        self.relay_manager = relay_manager
        
        self.instances: Dict[int, TorProcess] = {}
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
        
        self.global_suspicious_nodes: Set[str] = set()
        self.global_blacklisted_nodes: Set[str] = set()
        
    def start(self, instance_count: int, max_concurrent: int = 10, max_retries: int = 3) -> bool:
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

        all_nodes = list(self.relay_manager.exit_nodes_by_probability)
        used_nodes = set()

        def get_node_distributions(count, nodes_to_distribute):
            available_nodes = [n for n in nodes_to_distribute if n['ip'] not in used_nodes]
            logger.info(f"Distributing {len(available_nodes)} available nodes for {count} instances.")
            
            if not available_nodes:
                logger.warning("No more available nodes to distribute.")
                return {}

            distributions = self.relay_manager.distribute_exit_nodes_for_specific_instances(
                list(range(count)), available_nodes
            )
            
            for dist_data in distributions.values():
                for ip in dist_data.get('exit_nodes', []):
                    used_nodes.add(ip)
            return distributions

        # Initial attempt
        node_distributions = get_node_distributions(instance_count, all_nodes)
        if not node_distributions:
            logger.error("Failed to create initial node distribution.")
            return False
            
        processes_with_nodes = sum(1 for d in node_distributions.values() if d.get('exit_nodes'))
        logger.info(f"Created initial node distributions for {processes_with_nodes}/{instance_count} processes")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            failed_ids = loop.run_until_complete(
                self._create_instances_async(node_distributions, max_concurrent)
            )

            # Retry logic
            for attempt in range(max_retries):
                if not failed_ids:
                    break
                
                logger.info(f"Retry attempt {attempt + 1}/{max_retries} for {len(failed_ids)} failed instances.")
                
                retry_distributions = get_node_distributions(len(failed_ids), all_nodes)
                
                if not retry_distributions:
                    logger.warning("No nodes available for retry, stopping.")
                    break

                # Map new distributions to original failed IDs
                mapped_retry_distributions = {
                    original_id: retry_distributions[i]
                    for i, original_id in enumerate(failed_ids)
                }

                failed_ids = loop.run_until_complete(
                    self._create_instances_async(mapped_retry_distributions, max_concurrent)
                )

        finally:
            loop.close()

        with self._lock:
            final_count = len(self.instances)
            if final_count == 0:
                logger.error("No instances could be started.")
                return False

            self.running = True
            self._start_cleanup_thread()
            self._update_stats()

        logger.info(f"Pool started successfully with {final_count}/{instance_count} instances.")
        return True

    async def _create_instances_async(
        self, 
        node_distributions: Dict[int, Dict], 
        max_concurrent: int
    ) -> List[int]:
        if not node_distributions:
            return []

        logger.info(f"Starting parallel creation of {len(node_distributions)} Tor instances with max_concurrent={max_concurrent}")
        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = []
        
        for process_id, dist_data in node_distributions.items():
            exit_nodes = dist_data.get('exit_nodes', [])
            if not exit_nodes:
                logger.warning(f"Skipping instance {process_id} due to no assigned exit nodes.")
                continue
            
            task = self._create_single_instance(semaphore, process_id, exit_nodes)
            tasks.append(task)
            
        logger.info(f"Created {len(tasks)} tasks for parallel execution")
        results = await asyncio.gather(*tasks)
        
        failed_ids = [result for result in results if result is not None]
        
        successful_count = len(node_distributions) - len(failed_ids)
        logger.info(f"Parallel instance creation completed: {successful_count}/{len(node_distributions)} successful, {len(failed_ids)} failed")
        
        return failed_ids

    async def _create_single_instance(self, semaphore: asyncio.Semaphore, process_id: int, exit_nodes: List[str]) -> Optional[int]:
        async with semaphore:
            port = self.next_port
            self.next_port += 1
            
            logger.info(f"[{process_id}] Starting creation of Tor process on port {port} with {len(exit_nodes)} exit nodes")
            
            instance = TorProcess(
                port=port,
                exit_nodes=exit_nodes
            )

            if not instance.create_config(self.config_manager):
                logger.error(f"[{process_id}] Tor process on port {port} failed to create config")
                return process_id
                
            if not instance.start_process():
                logger.error(f"[{process_id}] Tor process on port {port} failed to start")
                return process_id
                
            if not await self._wait_for_startup_async(instance):
                logger.error(f"[{process_id}] Tor process on port {port} failed to start properly")
                instance.stop_process()
                instance.cleanup()
                return process_id

            instance.is_running = True

            logger.info(f"[{process_id}] Tor process on port {port} started successfully")
            with self._lock:
                self.instances[port] = instance
                self.load_balancer.add_proxy(port)
                self.added_to_balancer.add(port)
            return None

    def stop(self):
        with self._lock:
            if not self.running:
                return
                
            self.running = False
            self._shutdown_event.set()
            
            if self._cleanup_thread:
                safe_stop_thread(self._cleanup_thread)
                
            instances_to_stop = list(self.instances.values())
            
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(self._stop_instances_async(instances_to_stop))
        finally:
            loop.close()
            
        with self._lock:         
            self.instances.clear()
            self.added_to_balancer.clear()
            self.global_suspicious_nodes.clear()
            self.global_blacklisted_nodes.clear()
            
        self._update_stats()

    async def _stop_instances_async(self, instances_to_stop: List[TorProcess]):
        if not instances_to_stop:
            return
        logger.info(f"Stopping {len(instances_to_stop)} Tor instances...")
        for instance in instances_to_stop:
            instance.stop_process()
            instance.cleanup()
        logger.info("All specified Tor instances have been stopped.")

    def get_stats(self) -> dict:
        with self._lock:
            base_stats = self.stats.copy()
            exit_stats = self._get_exit_node_global_stats()
            base_stats['exit_node_monitoring'] = exit_stats
            return base_stats

    def get_instance_statuses(self) -> Generator[dict, None, None]:
        with self._lock:
            for instance in self.instances.values():
                yield instance.get_status()
            
    def _create_instance(self, port: int, exit_nodes: List[str]) -> Optional[TorProcess]:
        logger.info(f"Creating Tor process on port {port} with {len(exit_nodes)} exit nodes")
        instance = TorProcess(port=port, exit_nodes=exit_nodes)

        if not instance.create_config(self.config_manager):
            logger.error(f"Failed to create config for port {port}")
            return None

        if not instance.start_process():
            logger.error(f"Failed to start Tor process on port {port}")
            instance.cleanup()
            return None

        if not self._wait_for_startup(instance):
            logger.error(f"Tor process on port {port} failed to start properly")
            instance.stop_process()
            instance.cleanup()
            return None

        instance.is_running = True
        logger.info(f"Tor process started successfully on port {port}")
        return instance

    def _wait_for_startup(self, instance: TorProcess, timeout: int = 60) -> bool:
        logger.info(f"Waiting for Tor process on port {instance.port} to start up...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if instance.process and instance.process.poll() is not None:
                logger.error(f"Tor process on port {instance.port} died during startup")
                return False

            if instance.test_connection():
                logger.info(f"Tor process on port {instance.port} is ready")
                return True

            time.sleep(2)

        logger.warning(f"Tor process on port {instance.port} failed to start within {timeout}s")
        return False

    async def _wait_for_startup_async(self, instance: TorProcess, timeout: int = 60) -> bool:
        logger.info(f"Waiting for Tor process on port {instance.port} to start up...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            if instance.process and instance.process.poll() is not None:
                logger.error(f"Tor process on port {instance.port} died during startup")
                return False

            if instance.test_connection():
                logger.info(f"Tor process on port {instance.port} is ready")
                return True

            await asyncio.sleep(2)

        logger.warning(f"Tor process on port {instance.port} failed to start within {timeout}s")
        return False

    def _remove_instance(self, port: int):
        with self._lock:
            if port in self.instances:
                instance = self.instances.pop(port)
                instance.stop_process()
                instance.cleanup()
                
        self._remove_from_load_balancer(port)
        
    def _get_next_port(self) -> int:
        with self._lock:
            while self.next_port in self.instances:
                self.next_port += 1
            port = self.next_port
            self.next_port += 1
            return port
            
    def _add_to_load_balancer(self, port: int):
        if port not in self.added_to_balancer:
            logger.info(f"Adding port {port} to load balancer...")
            self.load_balancer.add_proxy(port)
            self.added_to_balancer.add(port)
            
    def _remove_from_load_balancer(self, port: int):
        if port in self.added_to_balancer:
            self.load_balancer.remove_proxy(port)
            self.added_to_balancer.discard(port)
            
    def _update_load_balancer(self):
        with self._lock:
            healthy_count = 0
            total_instances = len(self.instances)
            
            for port, instance in self.instances.items():
                if instance.is_running and port not in self.added_to_balancer:
                    is_healthy = instance.check_health()
                    
                    if is_healthy or instance.failed_checks >= 10:
                        self.load_balancer.add_proxy(port)
                        self.added_to_balancer.add(port)
                        healthy_count += 1
                elif not instance.is_running and port in self.added_to_balancer:
                    self._remove_from_load_balancer(port)
                        
            logger.info(f"Load balancer update: {healthy_count} new instances added, {len(self.added_to_balancer)} total")
        
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
        
        cycle_counter = 0
        
        while not self._shutdown_event.is_set() and self.running:
            self._check_dead_instances()
            self._update_stats()
            
            if cycle_counter % 1 == 0:
                self._check_all_instances_health()
                self._update_load_balancer()
            
            if cycle_counter % 2 == 0:
                self._update_global_exit_node_monitoring()
            
            if cycle_counter % 5 == 0:
                self.redistribute_nodes()
                
            cycle_counter += 1
            self._shutdown_event.wait(30)
                
        logger.debug("Stopped cleanup thread")
        
    def _check_all_instances_health(self):
        with self._lock:
            for port, instance in self.instances.items():
                if instance.is_running:
                    is_healthy = instance.check_health()
                    if not is_healthy and instance.failed_checks >= instance.max_failures:
                        logger.warning(f"Instance {port} failed health checks {instance.failed_checks} times, restarting...")
                        self._restart_instance(port)
                    
                    instance.check_inactive_exit_nodes()

    def _restart_instance(self, port: int) -> bool:
        with self._lock:
            if port not in self.instances:
                return False
                
            instance = self.instances[port]
            exit_nodes = instance.exit_nodes.copy()
            
            logger.info(f"Restarting Tor process on port {port}")
            
            if port in self.added_to_balancer:
                self._remove_from_load_balancer(port)
            
            instance.is_running = False
            instance.stop_process()
            instance.cleanup()
            time.sleep(2)
            
            if not instance.create_config(self.config_manager):
                logger.error(f"Failed to recreate config for port {port}")
                return False
                
            if not instance.start_process():
                logger.error(f"Failed to restart Tor process on port {port}")
                return False
                
            if not self._wait_for_startup(instance):
                logger.error(f"Restarted Tor process on port {port} failed to start properly")
                instance.stop_process()
                instance.cleanup()
                return False
                
            instance.is_running = True
            instance.failed_checks = 0
            logger.info(f"Successfully restarted Tor process on port {port}")
            
            return True
        
    def _check_dead_instances(self):
        dead_ports = []
        
        with self._lock:
            for port, instance in self.instances.items():
                if not instance.is_running or (instance.process and instance.process.poll() is not None):
                    dead_ports.append(port)

        for port in dead_ports:
            logger.warning(f"Found dead instance on port {port}, removing")
            self._remove_instance(port)

    def _update_stats(self):
        with self._lock:
            self.stats['total_instances'] = len(self.instances)
            self.stats['running_instances'] = sum(1 for instance in self.instances.values() if instance.is_running)
            self.stats['last_update'] = datetime.now()

    def _stop_single_instance(self, instance):
        instance.is_running = False
        instance.stop_process()
        instance.cleanup()
    
    def _get_exit_node_global_stats(self) -> dict:
        with self._lock:
            total_tracked = total_active = total_inactive = 0
            total_suspicious = total_blacklisted = 0
            
            for instance in self.instances.values():
                stats = instance.get_exit_node_stats()
                total_tracked += stats['total_tracked_nodes']
                total_active += stats['active_nodes']
                total_inactive += stats['inactive_nodes']
                total_suspicious += stats['suspicious_nodes']
                total_blacklisted += stats['blacklisted_nodes']
            
            return {
                'total_tracked_nodes': total_tracked,
                'active_nodes': total_active,
                'inactive_nodes': total_inactive,
                'suspicious_nodes': total_suspicious,
                'blacklisted_nodes': total_blacklisted,
                'global_suspicious_count': len(self.global_suspicious_nodes),
                'global_blacklisted_count': len(self.global_blacklisted_nodes)
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
                for node in instance.suspicious_nodes:
                    if node not in self.global_suspicious_nodes and node not in self.global_blacklisted_nodes:
                        newly_suspicious.add(node)
                        
            self.global_suspicious_nodes.update(newly_suspicious)
            
            if newly_suspicious:
                logger.warning(f"Added {len(newly_suspicious)} nodes to global suspicious list")

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
            relay_data = None
            new_exit_nodes = None
            
            for port, instance, healthy_nodes in healthy_instances:
                if len(healthy_nodes) < len(instance.exit_nodes) * 0.5:
                    logger.info(f"Instance {port} has low healthy nodes ratio: {len(healthy_nodes)}/{len(instance.exit_nodes)}")
                    
                    if relay_data is None:
                        relay_data = self.relay_manager.fetch_tor_relays()
                        if relay_data:
                            new_exit_nodes = self.relay_manager.extract_relay_ips(relay_data)
                    
                    if new_exit_nodes:
                        filtered_nodes = [node['ip'] for node in new_exit_nodes 
                                        if node['ip'] not in self.global_blacklisted_nodes 
                                        and node['ip'] not in self.global_suspicious_nodes]
                        
                        if len(filtered_nodes) > len(instance.exit_nodes):
                            if instance.reload_exit_nodes(filtered_nodes[:len(instance.exit_nodes)], self.config_manager):
                                redistributed_count += 1
                                logger.info(f"Redistributed nodes for instance {port}")
            
            if redistributed_count > 0:
                logger.info(f"Completed redistribution for {redistributed_count} instances")
                
    def _add_all_instances_to_balancer(self):
        with self._lock:
            added_count = 0
            total_running = 0
            
            for port, instance in self.instances.items():
                if instance.is_running:
                    total_running += 1
                    if port not in self.added_to_balancer:
                        self.load_balancer.add_proxy(port)
                        self.added_to_balancer.add(port)
                        added_count += 1
            
            logger.info(f"Added {added_count} instances to load balancer")
            logger.info(f"Status: {total_running} total running, {len(self.added_to_balancer)} in balancer")
