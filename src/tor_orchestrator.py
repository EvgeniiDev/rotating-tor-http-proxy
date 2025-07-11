import logging
import threading
import time
from typing import List, Dict, Optional, Any
from datetime import datetime

from tor_config_builder import TorConfigBuilder
from tor_process_manager import TorProcessManager
from new_tor_pool_manager import TorPoolManager
from exit_node_validator import ExitNodeValidator
from tor_relay_manager import TorRelayManager
from http_load_balancer import HTTPLoadBalancer

logger = logging.getLogger(__name__)


class TorOrchestrator:
    
    def __init__(self, listen_port: int = 8081, data_dir: Optional[str] = None):
        self.config_builder = TorConfigBuilder(data_dir)
        self.pool_manager = TorPoolManager(self.config_builder, max_concurrent=20)
        self.node_validator = ExitNodeValidator(self.config_builder, max_workers=25)
        self.relay_manager = TorRelayManager()
        self.load_balancer = HTTPLoadBalancer(listen_port)
        
        self.listen_port = listen_port
        self.data_dir = data_dir
        self.max_concurrent_processes = 20
        self.max_nodes_per_process = 25
        
        self.is_running = False
        self.validated_nodes: List[str] = []
        self.active_processes: Dict[int, Dict] = {}
        
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()
        
        self._maintenance_thread: Optional[threading.Thread] = None
        
        self.stats = {
            'total_nodes_fetched': 0,
            'validated_nodes': 0,
            'active_processes': 0,
            'load_balancer_processes': 0,
            'last_update': None,
            'system_status': 'stopped'
        }
    
    def start_system(self, process_count: int, validate_nodes: bool = True) -> bool:
        if self.is_running:
            logger.warning("System is already running")
            return True
        
        logger.info(f"Starting Tor proxy system with {process_count} processes")
        
        try:
            if not self._start_load_balancer():
                return False
            
            exit_nodes = self._fetch_exit_nodes()
            if not exit_nodes:
                logger.error("Failed to fetch exit nodes")
                self._stop_load_balancer()
                return False
            
            if validate_nodes:
                validated_nodes = self._validate_nodes(exit_nodes)
                if not validated_nodes:
                    logger.error("No valid exit nodes found after validation")
                    self._stop_load_balancer()
                    return False
                self.validated_nodes = validated_nodes
            else:
                self.validated_nodes = [node['ip'] for node in exit_nodes]
            
            process_configs = self._distribute_nodes_to_processes(process_count)
            if not process_configs:
                logger.error("Failed to distribute nodes to processes")
                self._stop_load_balancer()
                return False
            
            start_result = self.pool_manager.start_processes(process_configs)
            successful_ports = start_result['successful']
            
            if not successful_ports:
                logger.error("No Tor processes started successfully")
                self._stop_load_balancer()
                return False
            
            for port in successful_ports:
                self._add_process_to_balancer(port)
            
            self._start_maintenance()
            
            self.is_running = True
            self._update_stats()
            
            logger.info(f"System started successfully with {len(successful_ports)}/{process_count} processes")
            logger.info(f"HTTP proxy available at: http://localhost:{self.listen_port}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start system: {e}")
            self.stop_system()
            return False
    
    def stop_system(self):
        if not self.is_running:
            logger.info("System is not running")
            return
        
        logger.info("Stopping Tor proxy system...")
        
        self.is_running = False
        self._shutdown_event.set()
        
        if self._maintenance_thread and self._maintenance_thread.is_alive():
            self._maintenance_thread.join(timeout=10)
        
        self.pool_manager.stop_all_processes()
        
        self._stop_load_balancer()
        
        with self._lock:
            self.validated_nodes.clear()
            self.active_processes.clear()
        
        self._update_stats()
        logger.info("System stopped successfully")
    
    def get_system_status(self) -> Dict[str, Any]:
        with self._lock:
            system_stats = self.stats.copy()
            
            system_stats.update({
                'pool_manager': self.pool_manager.get_stats(),
                'load_balancer': self.load_balancer.get_stats(),
                'node_validator': self.node_validator.get_validation_stats(),
                'process_statuses': self.pool_manager.get_all_statuses(),
                'validated_nodes_count': len(self.validated_nodes),
                'listen_port': self.listen_port
            })
            
            return system_stats
    
    def restart_failed_processes(self) -> Dict[str, List[int]]:
        logger.info("Restarting failed processes...")
        result = self.pool_manager.restart_failed_processes()
        
        for port in result['successful']:
            self._add_process_to_balancer(port)
        
        self._update_stats()
        return result
    
    def add_more_processes(self, additional_count: int) -> Dict[str, List[int]]:
        if additional_count <= 0:
            return {'successful': [], 'failed': []}
        
        current_processes = len(self.active_processes)
        if current_processes + additional_count > self.max_concurrent_processes:
            additional_count = self.max_concurrent_processes - current_processes
            logger.warning(f"Limiting additional processes to {additional_count} "
                         f"(max concurrent: {self.max_concurrent_processes})")
        
        if additional_count <= 0:
            logger.warning("Cannot add more processes - at maximum limit")
            return {'successful': [], 'failed': []}
        
        remaining_configs = self._distribute_nodes_to_processes(additional_count)
        if not remaining_configs:
            logger.warning("No validated nodes available for additional processes")
            return {'successful': [], 'failed': []}
        
        result = self.pool_manager.start_processes(remaining_configs)
        
        for port in result['successful']:
            self._add_process_to_balancer(port)
        
        self._update_stats()
        return result
    
    def _start_load_balancer(self) -> bool:
        try:
            self.load_balancer.start()
            logger.info(f"HTTP Load Balancer started on port {self.listen_port}")
            return True
        except Exception as e:
            logger.error(f"Failed to start load balancer: {e}")
            return False
    
    def _stop_load_balancer(self):
        try:
            self.load_balancer.stop()
            logger.info("HTTP Load Balancer stopped")
        except Exception as e:
            logger.error(f"Error stopping load balancer: {e}")
    
    def _fetch_exit_nodes(self) -> List[Dict]:
        logger.info("Fetching Tor exit nodes...")
        
        relay_data = self.relay_manager.fetch_tor_relays()
        if not relay_data:
            logger.error("Failed to fetch Tor relay data")
            return []
        
        exit_nodes = self.relay_manager.extract_relay_ips(relay_data)
        if not exit_nodes:
            logger.error("No exit nodes extracted from relay data")
            return []
        
        logger.info(f"Found {len(exit_nodes)} exit nodes")
        with self._lock:
            self.stats['total_nodes_fetched'] = len(exit_nodes)
        
        return exit_nodes
    
    def _validate_nodes(self, exit_nodes: List[Dict]) -> List[str]:
        logger.info(f"Validating {len(exit_nodes)} exit nodes...")
        
        node_ips = [node['ip'] for node in exit_nodes]
        validated_ips = self.node_validator.validate_exit_nodes(node_ips)
        
        with self._lock:
            self.stats['validated_nodes'] = len(validated_ips)
        
        logger.info(f"Validation completed: {len(validated_ips)}/{len(node_ips)} nodes passed")
        return validated_ips
    
    def _distribute_nodes_to_processes(self, process_count: int) -> List[Dict]:
        if not self.validated_nodes:
            logger.error("No validated nodes available for distribution")
            return []
        
        actual_process_count = min(process_count, self.max_concurrent_processes)
        if actual_process_count != process_count:
            logger.warning(f"Limiting process count to {actual_process_count}")
        
        nodes_per_process = len(self.validated_nodes) // actual_process_count
        nodes_per_process = min(nodes_per_process, self.max_nodes_per_process)
        
        if nodes_per_process == 0:
            logger.error("Not enough validated nodes for distribution")
            return []
        
        logger.info(f"Distributing {len(self.validated_nodes)} nodes to {actual_process_count} processes "
                   f"({nodes_per_process} nodes per process)")
        
        process_configs = []
        start_port = 10000
        
        for i in range(actual_process_count):
            start_idx = i * nodes_per_process
            end_idx = min(start_idx + nodes_per_process, len(self.validated_nodes))
            
            if start_idx >= len(self.validated_nodes):
                break
            
            process_nodes = self.validated_nodes[start_idx:end_idx]
            
            config = {
                'port': start_port + i,
                'exit_nodes': process_nodes
            }
            process_configs.append(config)
        
        logger.info(f"Created {len(process_configs)} process configurations")
        return process_configs
    
    def _add_process_to_balancer(self, port: int):
        try:
            self.load_balancer.add_proxy(port)
            
            with self._lock:
                self.active_processes[port] = {
                    'added_to_balancer': True,
                    'added_at': datetime.now()
                }
            
            logger.info(f"Added process on port {port} to load balancer")
            
        except Exception as e:
            logger.error(f"Failed to add process {port} to load balancer: {e}")
    
    def _remove_process_from_balancer(self, port: int):
        try:
            self.load_balancer.remove_proxy(port)
            
            with self._lock:
                if port in self.active_processes:
                    del self.active_processes[port]
            
            logger.info(f"Removed process on port {port} from load balancer")
            
        except Exception as e:
            logger.error(f"Failed to remove process {port} from load balancer: {e}")
    
    def _start_maintenance(self):
        if self._maintenance_thread and self._maintenance_thread.is_alive():
            return
        
        self._maintenance_thread = threading.Thread(
            target=self._maintenance_loop,
            name="TorOrchestratorMaintenance"
        )
        self._maintenance_thread.daemon = True
        self._maintenance_thread.start()
    
    def _maintenance_loop(self):
        logger.debug("Started system maintenance")
        
        cycle_count = 0
        
        while not self._shutdown_event.is_set() and self.is_running:
            try:
                self._update_stats()
                
                if cycle_count % 4 == 0:
                    self._check_process_health()
                
                if cycle_count % 10 == 0:
                    self.restart_failed_processes()
                
                cycle_count += 1
                
            except Exception as e:
                logger.error(f"Error in maintenance loop: {e}")
            
            self._shutdown_event.wait(30)
        
        logger.debug("Stopped system maintenance")
    
    def _check_process_health(self):
        unhealthy_ports = []
        
        with self._lock:
            active_ports = list(self.active_processes.keys())
        
        for port in active_ports:
            if not self.pool_manager.check_process_health(port):
                unhealthy_ports.append(port)
        
        for port in unhealthy_ports:
            self._remove_process_from_balancer(port)
    
    def _update_stats(self):
        with self._lock:
            pool_stats = self.pool_manager.get_stats()
            
            self.stats.update({
                'active_processes': pool_stats.get('running_processes', 0),
                'load_balancer_processes': len(self.active_processes),
                'last_update': datetime.now(),
                'system_status': 'running' if self.is_running else 'stopped'
            })