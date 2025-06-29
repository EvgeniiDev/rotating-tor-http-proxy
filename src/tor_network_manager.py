import time
import logging
import threading
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
        self.active_subnets = set()
        self.subnet_limits = {}
        self.monitoring = True
        self.services_started = False
        self.load_balancer = load_balancer
        self.config_manager = ConfigManager()
        self.socketio = socketio
        self._subnet_lock = threading.RLock()
        
        self.relay_manager = TorRelayManager()
        self.process_manager = TorProcessManager(self.config_manager, load_balancer)
        self.health_monitor = TorHealthMonitor(
            self._restart_tor_instance_by_port,
            get_available_subnets_callback=self._get_available_subnets_for_health_monitor
        )
        
        self.stats = {
            'active_subnets': 0,
            'blocked_subnets': 0,
            'last_update': None,
            'tor_instances': 0,
            'running_instances': 0
        }

    def fetch_tor_relays(self):
        return self.relay_manager.fetch_tor_relays()

    def extract_relay_ips(self, relay_data):
        return self.relay_manager.extract_relay_ips(relay_data)

    def get_available_subnets(self, count=None):
        return self.relay_manager.get_available_subnets(count)

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
        available_subnets = self.get_available_subnets(count)
        
        if not available_subnets:
            relay_data = self.fetch_tor_relays()
            if relay_data:
                self.extract_relay_ips(relay_data)
                available_subnets = self.get_available_subnets(count)
        
        if not available_subnets:
            logger.warning("No subnets available for Tor instances")
            return
            
        logger.info(f"Using subnets: {available_subnets}")
        
        batch_size = 20
        total_started = 0
        
        for batch_start in range(0, count, batch_size):
            batch_end = min(batch_start + batch_size, count)
            batch_subnets = available_subnets[batch_start:batch_end]
            
            logger.info(f"Starting batch {batch_start//batch_size + 1}: instances {batch_start + 1}-{batch_end}")
            
            batch_ports = []
            for i, subnet in enumerate(batch_subnets):
                try:
                    port = self.process_manager.start_tor_instance(subnet)
                    if port:
                        self.health_monitor.add_instance(port, subnet)
                        batch_ports.append(port)
                        total_started += 1
                        logger.info(f"Started Tor instance on port {port} with subnet {subnet}")
                    else:
                        logger.warning(f"Failed to start Tor instance with subnet {subnet}")
                except Exception as e:
                    logger.error(f"Error starting Tor instance {batch_start + i + 1} with subnet {subnet}: {e}")
            
            if batch_end < count and batch_ports:
                logger.info(f"Batch {batch_start//batch_size + 1} completed. Waiting for instances to be ready...")
                self._wait_for_batch_ready(batch_ports)
        
        logger.info(f"Auto-start completed: {total_started}/{count} instances started successfully")

    def _wait_for_batch_ready(self, batch_ports, max_wait_time=120, check_interval=2):
        start_time = time.time()
        
        while time.time() - start_time < max_wait_time:
            ready_ports = []
            for port in batch_ports:
                if self.health_monitor.is_instance_ready(port):
                    ready_ports.append(port)
            
            if len(ready_ports) == len(batch_ports):
                logger.info(f"All {len(batch_ports)} instances in batch are ready")
                return True
            
            if len(ready_ports) > 0:
                logger.info(f"{len(ready_ports)}/{len(batch_ports)} instances ready, waiting for remaining...")
            
            time.sleep(check_interval)
        
        ready_count = sum(1 for port in batch_ports if self.health_monitor.is_instance_ready(port))
        logger.warning(f"Batch readiness timeout: {ready_count}/{len(batch_ports)} instances ready after {max_wait_time}s")
        return ready_count > 0

    def stop_services(self):
        with self._subnet_lock:
            self.health_monitor.stop()
            self.process_manager.stop_all_instances()
            
            self.active_subnets.clear()
            self.subnet_limits.clear()
            self.health_monitor.clear()
            
            self.services_started = False
            self.update_running_instances_count()
            logger.info("All services stopped and HTTP load balancer cleared")
            return True

    def update_running_instances_count(self):
        running_main, running_subnet = self.process_manager.count_running_instances()
        self.stats['running_instances'] = running_main + running_subnet
        self.stats['tor_instances'] = running_main + running_subnet

    def get_service_status(self):
        running_main, running_subnet = self.process_manager.count_running_instances()
        total_running = running_main + running_subnet
        failed_instances = self.process_manager.get_failed_instances()

        status = ServiceStatus(
            services_started=self.services_started,
            total_instances=len(self.process_manager.get_all_ports()),
            running_tor=total_running,
            running_socks=total_running,
            failed_instances=failed_instances,
            last_check=get_current_timestamp()
        )
        return status.to_dict()

    def update_subnet_stats(self):
        relays = self.relay_manager.current_relays
        if not relays:
            relay_data = self.fetch_tor_relays()
            if relay_data:
                relays = self.extract_relay_ips(relay_data)

        subnet_counts = defaultdict(int)
        for relay in relays or []:
            ip_parts = relay['ip'].split('.')
            if len(ip_parts) >= 2:
                subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                subnet_counts[subnet] += 1

        active_count = sum(
            1 for subnet in subnet_counts if subnet in self.active_subnets)

        occupied_count = 0
        free_count = 0

        for subnet in subnet_counts:
            running_instances = self.process_manager.get_subnet_running_instances(subnet)
            if running_instances > 0:
                occupied_count += 1
            else:
                free_count += 1

        blocked_count = sum(1 for count in subnet_counts.values() if count < 5)

        self.stats.update({
            'active_subnets': active_count,
            'blocked_subnets': blocked_count,
            'occupied_subnets': occupied_count,
            'free_subnets': free_count,
            'total_subnets': len(subnet_counts),
            'last_update': datetime.now().isoformat()
        })

    def emit_subnet_data(self, relays):
        if not self.socketio:
            return

        subnet_counts, subnet_details = self.relay_manager.get_subnet_details()
        sorted_subnets = sorted(subnet_counts.items(), key=lambda x: x[1], reverse=True)

        subnet_data = []
        for subnet, count in sorted_subnets:
            status = 'active' if subnet in self.active_subnets else 'available'
            limit = self.subnet_limits.get(subnet, 1)
            running_instances = self.process_manager.get_subnet_running_instances(subnet)

            subnet_data.append({
                'subnet': subnet,
                'count': count,
                'status': status,
                'limit': limit,
                'running_instances': running_instances,
                'relays': subnet_details[subnet][:5]
            })

        self.socketio.emit('subnet_data', {
            'subnets': subnet_data,
            'stats': {
                'active_subnets': self.stats.get('active_subnets', 0),
                'blocked_subnets': self.stats.get('blocked_subnets', 0),
                'occupied_subnets': self.stats.get('occupied_subnets', 0),
                'free_subnets': self.stats.get('free_subnets', 0),
                'total_subnets': self.stats.get('total_subnets', 0),
                'running_instances': self.stats.get('running_instances', 0),
                'last_update': self.stats.get('last_update')
            }
        })

    def start_monitoring(self):
        def monitor():
            while self.monitoring:
                relay_data = self.fetch_tor_relays()
                if relay_data:
                    relays = self.extract_relay_ips(relay_data)
                    self.update_subnet_stats()
                    self.emit_subnet_data(relays)
                    logger.info(f"Fetched {len(relays)} Tor relay IPs")
                else:
                    logger.warning("Failed to fetch relay data")

                for _ in range(300):
                    if not self.monitoring:
                        break
                    time.sleep(1)
        monitor_thread = threading.Thread(target=monitor)
        monitor_thread.daemon = True
        monitor_thread.start()
        logger.info("Started monitoring thread")

    def stop_monitoring(self):
        self.monitoring = False

    def _start_subnet_tor_internal(self, subnet, instances_count=1):
        success, started_ports = self.process_manager.start_subnet_instances(subnet, instances_count)
        
        if success:
            for port in started_ports:
                self.health_monitor.add_instance(port, subnet)
            
            self.active_subnets.add(subnet)
            self.subnet_limits[subnet] = instances_count
            self.update_running_instances_count()
            logger.info(f"Started {instances_count} Tor instances for subnet {subnet}")
        
        return success

    def start_subnet_tor(self, subnet, instances_count=1):
        with self._subnet_lock:
            return self._start_subnet_tor_internal(subnet, instances_count)

    def _stop_subnet_tor_internal(self, subnet):
        success = self.process_manager.stop_subnet_instances(subnet)
        
        if success:
            self.active_subnets.discard(subnet)
            self.subnet_limits.pop(subnet, None)
            self.update_running_instances_count()
        
        return success

    def stop_subnet_tor(self, subnet):
        with self._subnet_lock:
            return self._stop_subnet_tor_internal(subnet)

    def restart_subnet_tor(self, subnet, instances_count=1):
        with self._subnet_lock:
            self._stop_subnet_tor_internal(subnet)
            time.sleep(2)
            return self._start_subnet_tor_internal(subnet, instances_count)

    def get_subnet_running_instances(self, subnet):
        return self.process_manager.get_subnet_running_instances(subnet)

    def get_load_balancer_stats(self):
        try:
            return self.load_balancer.get_stats()
        except Exception as e:
            logger.error(f"Error getting load balancer stats: {e}")
            return {}

    def get_comprehensive_stats(self):
        tor_stats = {
            'tor_network': {
                'services_started': self.services_started,
                'active_subnets': len(self.active_subnets),
                'total_instances': len(self.process_manager.get_all_ports()),
                'running_instances': self.stats.get('running_instances', 0),
                'tor_instances': self.stats.get('tor_instances', 0),
                'subnet_limits': dict(self.subnet_limits),
                'active_subnet_list': list(self.active_subnets),
                'last_update': self.stats.get('last_update')
            }
        }
        
        lb_stats = self.get_load_balancer_stats()
        if lb_stats:
            tor_stats['http_load_balancer'] = lb_stats
        
        health_stats = self.health_monitor.get_stats()
        if health_stats:
            tor_stats['health_monitoring'] = health_stats
            
        return tor_stats

    def _get_available_subnets_for_health_monitor(self, count=1, exclude=None):
        exclude = exclude or set()
        available_subnets = self.get_available_subnets(count * 2)
        
        filtered_subnets = []
        for subnet in available_subnets:
            if subnet not in exclude and subnet not in self.active_subnets:
                filtered_subnets.append(subnet)
                if len(filtered_subnets) >= count:
                    break
        
        if not filtered_subnets and not available_subnets:
            relay_data = self.fetch_tor_relays()
            if relay_data:
                self.extract_relay_ips(relay_data)
                available_subnets = self.get_available_subnets(count * 2)
                
                for subnet in available_subnets:
                    if subnet not in exclude and subnet not in self.active_subnets:
                        filtered_subnets.append(subnet)
                        if len(filtered_subnets) >= count:
                            break
        
        return filtered_subnets[:count]

    def _restart_tor_instance_by_port(self, port, subnet):
        return self.process_manager.restart_instance_by_port(port, subnet)

    def get_health_stats(self):
        return self.health_monitor.get_stats()
