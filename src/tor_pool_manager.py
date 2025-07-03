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
        
        if final_count == 0:
            return False
            
        with self._lock:
            self.running = True
            
        self._start_cleanup_thread()
        self._update_stats()
        logger.info(f"Pool started successfully with {final_count} instances")
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
            
        with self._lock:         
            self.instances.clear()
            self.added_to_balancer.clear()
            self.global_suspicious_nodes.clear()
            self.global_blacklisted_nodes.clear()
            
        self._update_stats()

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
            
    async def _create_instances_async(self, instance_count: int, node_distributions: dict, max_concurrent: int) -> int:
        semaphore = asyncio.Semaphore(max_concurrent)
        tasks = []

        for process_id in range(instance_count):
            if process_id not in node_distributions:
                continue
            
            process_exit_nodes = node_distributions[process_id].get('exit_nodes')
            if not process_exit_nodes:
                continue

            port = self._get_next_port()
            task = self._create_instance_async(semaphore, port, process_exit_nodes)
            tasks.append(task)

        if not tasks:
            return 0

        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        completed_count = 0
        for result in results:
            if isinstance(result, TorProcess):
                with self._lock:
                    self.instances[result.port] = result
                completed_count += 1
            elif isinstance(result, Exception):
                logger.error(f"Instance creation failed: {result}")

        logger.info(f"Async instance creation completed: {completed_count}/{len(tasks)} successful")

        if completed_count > 0:
            self._add_all_instances_to_balancer()

        return completed_count

    async def _create_instance_async(self, semaphore: asyncio.Semaphore, port: int, exit_nodes: List[str]) -> Optional[TorProcess]:
        async with semaphore:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self._create_instance, port, exit_nodes
            )

    async def _stop_instances_async(self, instances_to_stop: List[TorProcess]):
        if not instances_to_stop:
            return
            
        tasks = [
            asyncio.get_event_loop().run_in_executor(None, self._stop_single_instance, instance)
            for instance in instances_to_stop
        ]
        
        try:
            await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Timeout while stopping instances")

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
