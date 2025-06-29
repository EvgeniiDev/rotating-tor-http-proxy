import time
import logging
import threading
import requests
import concurrent.futures
from datetime import datetime
from collections import defaultdict

from config_manager import ConfigManager
from models import ServiceStatus, get_current_timestamp
from tor_health_monitor import TorHealthMonitor
from tor_relay_manager import TorRelayManager
from tor_process_manager import TorProcessManager

logger = logging.getLogger(__name__)


class TorNetworkManager:
    def __init__(self, socketio, load_balancer):
        self.active_processes = {}
        self.monitoring = True
        self.services_started = False
        self.load_balancer = load_balancer
        self.config_manager = ConfigManager()
        self.socketio = socketio
        self._lock = threading.RLock()

        self.relay_manager = TorRelayManager()
        self.process_manager = TorProcessManager(
            self.config_manager, load_balancer)
        self.health_monitor = TorHealthMonitor(
            self._restart_tor_instance_by_port,
            get_available_exit_nodes_callback=self._get_available_exit_nodes_for_health_monitor
        )

        self.stats = {
            'active_processes': 0,
            'last_update': None,
            'tor_instances': 0,
            'running_instances': 0,
            'total_exit_nodes': 0,
            'distributed_processes': 0
        }

    def fetch_tor_relays(self):
        return self.relay_manager.fetch_tor_relays()

    def extract_relay_ips(self, relay_data):
        return self.relay_manager.extract_relay_ips(relay_data)

    def distribute_exit_nodes(self, num_processes):
        return self.relay_manager.distribute_exit_nodes(num_processes)

    def start_services(self, auto_start_count=None):
        if self.services_started:
            logger.info("Services infrastructure already initialized")
            return True
        logger.info("Initializing services infrastructure...")

        self.services_started = True
        self.stats['tor_instances'] = 0
        self.update_running_instances_count()

        self.health_monitor.start()

        logger.info("Services infrastructure initialized successfully.")

        if auto_start_count and auto_start_count > 0:
            logger.info(f"Auto-starting {auto_start_count} Tor instances...")
            self._auto_start_tor_instances(auto_start_count)

        return True

    def _auto_start_tor_instances(self, count):
        relay_data = self.fetch_tor_relays()
        if not relay_data:
            logger.error("No relay data available")
            return

        exit_nodes = self.extract_relay_ips(relay_data)
        if not exit_nodes:
            logger.error("No exit nodes available")
            return

        logger.info(f"Found {len(exit_nodes)} exit nodes")
        
        node_distributions = self.distribute_exit_nodes(count)
        if not node_distributions:
            logger.error("Failed to distribute exit nodes")
            return

        logger.info(f"Distributed exit nodes across {len(node_distributions)} processes")

        exit_nodes_list = []
        for process_id in range(count):
            if process_id in node_distributions:
                process_exit_nodes = node_distributions[process_id]['exit_nodes']
                if process_exit_nodes:
                    exit_nodes_list.append(process_exit_nodes)

        if not exit_nodes_list:
            logger.error("No valid exit node distributions found")
            return

        batch_size = 10
        total_started = 0
        failed_processes = 0

        logger.info(f"Starting {len(exit_nodes_list)} Tor instances in batches of {batch_size}")
        batch_results = self.process_manager.start_tor_instances_batch(exit_nodes_list, batch_size)

        successful_instances = []
        for result in batch_results:
            if result['success']:
                port = result['port']
                process_exit_nodes = result['exit_nodes']
                
                self.health_monitor.add_instance(port, process_exit_nodes)
                successful_instances.append((port, process_exit_nodes))
            else:
                logger.error(f"Failed to start Tor instance with {len(result['exit_nodes'])} exit nodes")
                failed_processes += 1

        if successful_instances:
            logger.info(f"Running health checks for {len(successful_instances)} instances in parallel...")
            health_results = self._check_tor_instances_health_batch(successful_instances)
            
            for port, exit_nodes in successful_instances:
                health_result = health_results.get(port, 'failed')
                
                if health_result == 'ready':
                    total_started += 1
                    logger.info(f"Successfully started Tor instance on port {port}")
                else:
                    logger.warning(f"Tor instance on port {port} failed health check")
                    self.health_monitor.remove_instance(port)
                    self.process_manager.stop_tor_instance(port)
                    failed_processes += 1

        logger.info(f"Auto-start completed: {total_started}/{len(exit_nodes_list)} instances started successfully")
        if failed_processes > 0:
            logger.warning(f"Failed to start {failed_processes} processes")

        self.stats['distributed_processes'] = len(node_distributions)
        self.stats['total_exit_nodes'] = len(exit_nodes)

    def _check_tor_instance_health_progressive(self, port, exit_nodes):
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                proxies = {
                    'http': f'socks5://127.0.0.1:{port}',
                    'https': f'socks5://127.0.0.1:{port}'
                }
                
                response = requests.get('https://httpbin.org/ip', 
                                      proxies=proxies, 
                                      timeout=10)
                
                if response.status_code == 200:
                    return 'ready'
                    
            except Exception as e:
                logger.debug(f"Health check attempt {attempt + 1} failed for port {port}: {e}")
                
            if attempt < max_attempts - 1:
                time.sleep(5)
        
        return 'failed'

    def _check_tor_instances_health_batch(self, port_exit_nodes_pairs):
        results = {}
        logger.info(f"Starting parallel health checks for {len(port_exit_nodes_pairs)} instances")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_port = {
                executor.submit(self._check_tor_instance_health_progressive, port, exit_nodes): port
                for port, exit_nodes in port_exit_nodes_pairs
            }
            
            completed = 0
            for future in concurrent.futures.as_completed(future_to_port):
                port = future_to_port[future]
                try:
                    health_result = future.result()
                    results[port] = health_result
                    completed += 1
                    if completed % 5 == 0:
                        logger.info(f"Health check progress: {completed}/{len(port_exit_nodes_pairs)} completed")
                except Exception as e:
                    logger.error(f"Exception during health check for port {port}: {e}")
                    results[port] = 'failed'
                    completed += 1
        
        logger.info(f"All health checks completed: {sum(1 for r in results.values() if r == 'ready')}/{len(port_exit_nodes_pairs)} passed")
        return results

    def _get_available_exit_nodes_for_health_monitor(self):
        if hasattr(self.relay_manager, 'current_relays'):
            return [node['ip'] for node in self.relay_manager.current_relays[:100]]
        return []

    def _restart_tor_instance_by_port(self, port):
        exit_nodes = self.process_manager.get_port_exit_nodes(port)
        if exit_nodes:
            return self.process_manager.restart_instance_by_port(port, exit_nodes)
        return False

    def stop_services(self):
        logger.info("Stopping Tor Network Manager services...")
        
        self.monitoring = False
        
        if hasattr(self, 'health_monitor') and self.health_monitor:
            self.health_monitor.stop()
        
        if hasattr(self, 'process_manager') and self.process_manager:
            self.process_manager.stop_all_instances()
        
        self.services_started = False
        logger.info("Tor Network Manager services stopped")

    def update_running_instances_count(self):
        if hasattr(self, 'process_manager'):
            running_count = self.process_manager.count_running_instances()
            self.stats['running_instances'] = running_count
            self.stats['last_update'] = get_current_timestamp()

    def get_stats(self):
        self.update_running_instances_count()
        return self.stats.copy()

    def get_distribution_stats(self):
        return self.relay_manager.get_distribution_stats()

    def get_running_instances(self):
        if hasattr(self, 'process_manager'):
            return self.process_manager.get_all_ports()
        return []

    def stop_instance_by_port(self, port):
        if hasattr(self, 'process_manager'):
            self.process_manager.stop_tor_instance(port)
            if hasattr(self, 'health_monitor'):
                self.health_monitor.remove_instance(port)

    def start_monitoring(self):
        logger.info("Starting monitoring services...")
        self.monitoring = True
        return True

    def get_service_status(self):
        stats = self.get_stats()
        return {
            'status': 'running' if self.services_started else 'stopped',
            'monitoring': self.monitoring,
            'running_instances': stats.get('running_instances', 0),
            'total_instances': stats.get('tor_instances', 0),
            'services_started': self.services_started
        }
