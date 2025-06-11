import time
import logging
import threading
import subprocess
import requests
from datetime import datetime
from collections import defaultdict

from haproxy_manager import HAProxyManager
from config_manager import ConfigManager
from models import ServiceStatus, get_current_timestamp

logger = logging.getLogger(__name__)


class TorNetworkManager:
    def __init__(self, socketio=None):
        self.active_subnets = set()
        self.subnet_limits = {}
        self.tor_instances = []
        self.monitoring = True
        self.current_relays = {}
        self.services_started = False
        self.tor_processes = {}
        self.subnet_tor_processes = {}
        self.haproxy_manager = HAProxyManager()
        self.config_manager = ConfigManager()
        self.next_instance_id = 1
        self.socketio = socketio
        self._subnet_lock = threading.Lock()  # Add thread protection
        self.stats = {
            'active_subnets': 0,
            'blocked_subnets': 0,
            'last_update': None,
            'tor_instances': 0,
            'running_instances': 0
        }

    def fetch_tor_relays(self):
        """Fetch current Tor relay information"""
        try:
            url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,exit_probability"
            response = requests.get(url, timeout=30)
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            logger.error(f"Error fetching Tor relays: {e}")
            return None

    def extract_relay_ips(self, relay_data):
        """Extract IP addresses from relay data and filter by exit probability"""
        relays = []
        if not relay_data or 'relays' not in relay_data:
            return relays

        # First pass: collect all relays with their subnet information
        subnet_relays = defaultdict(list)

        for relay in relay_data['relays']:
            if 'or_addresses' in relay:
                for addr in relay['or_addresses']:
                    ip = addr.split(':')[0]
                    if ':' not in ip:
                        ip_parts = ip.split('.')
                        if len(ip_parts) >= 2:
                            subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                            exit_prob = relay.get('exit_probability', 0)
                            # Convert to float if it's not already
                            if isinstance(exit_prob, str):
                                try:
                                    exit_prob = float(exit_prob)
                                except (ValueError, TypeError):
                                    exit_prob = 0

                            relay_info = {
                                'ip': ip,
                                'country': relay.get('country', 'Unknown'),
                                'exit_probability': exit_prob
                            }
                            subnet_relays[subnet].append(relay_info)

        # если у ноды exit_probability = 0, то это промежуточный узел
        valid_subnets = set()
        for subnet, subnet_relay_list in subnet_relays.items():
            if any(relay['exit_probability'] > 0 for relay in subnet_relay_list):
                valid_subnets.add(subnet)

        # Third pass: collect only relays from valid subnets
        for subnet in valid_subnets:
            relays.extend(subnet_relays[subnet])

        logger.info(
            f"Filtered to {len(valid_subnets)} subnets with exit probability > 0")
        return relays

    def start_services(self):
        """Initialize infrastructure without starting Tor instances"""
        if self.services_started:
            logger.info("Services infrastructure already initialized")
            return True

        logger.info("Initializing services infrastructure...")

        self.services_started = True
        self.stats['tor_instances'] = 0
        self.update_running_instances_count()

        logger.info("Services infrastructure initialized successfully.")
        return True

    def stop_services(self):
        """Stop all Tor instances"""
        with self._subnet_lock:
            for process in self.tor_processes.values():
                if process and process.poll() is None:
                    process.terminate()

            # Create a copy to avoid dictionary modification during iteration
            subnet_processes_copy = dict(self.subnet_tor_processes)
            for processes in subnet_processes_copy.values():
                for process in processes.values():
                    if process and process.poll() is None:
                        process.terminate()

            self.tor_processes.clear()
            self.subnet_tor_processes.clear()
            self.active_subnets.clear()
            self.services_started = False
            self.update_running_instances_count()
            logger.info("All services stopped")
            return True

    def _count_running_instances(self):
        """Count running instances for both main and subnet processes"""
        running_main = sum(
            1 for p in self.tor_processes.values() if p and p.poll() is None)
        running_subnet = sum(
            sum(1 for p in processes.values() if p and p.poll() is None)
            for processes in self.subnet_tor_processes.values()
        )
        return running_main, running_subnet

    def get_service_status(self):
        """Check status of all running services"""
        running_main, running_subnet = self._count_running_instances()
        total_running = running_main + running_subnet

        failed_instances = []
        for instance_id, process in self.tor_processes.items():
            if not (process and process.poll() is None):
                failed_instances.append(f"tor-{instance_id}")

        for subnet_key, processes in self.subnet_tor_processes.items():
            for instance_id, process in processes.items():
                if not (process and process.poll() is None):
                    failed_instances.append(f"subnet-tor-{instance_id}")

        status = ServiceStatus(
            services_started=self.services_started,
            total_instances=len(self.tor_processes) + sum(len(p)
                                                          for p in self.subnet_tor_processes.values()),
            running_tor=total_running,
            running_socks=total_running,
            failed_instances=failed_instances,
            last_check=get_current_timestamp()
        )
        return status.to_dict()

    def update_subnet_stats(self):
        """Update subnet statistics"""
        if not self.current_relays:
            relay_data = self.fetch_tor_relays()
            if relay_data:
                self.current_relays = self.extract_relay_ips(relay_data)

        subnet_counts = defaultdict(int)
        for relay in self.current_relays or []:
            ip_parts = relay['ip'].split('.')
            if len(ip_parts) >= 2:
                subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                subnet_counts[subnet] += 1

        # Count active subnets (those with running instances)
        active_count = sum(
            1 for subnet in subnet_counts if subnet in self.active_subnets)

        # Count subnets with running instances
        occupied_count = 0
        free_count = 0

        for subnet in subnet_counts:
            subnet_key = f"subnet_{subnet.replace('.', '_')}"
            running_instances = len([
                p for p in self.subnet_tor_processes.get(subnet_key, {}).values()
                if p and p.poll() is None
            ])

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
        """Emit subnet information to WebSocket clients"""
        if not self.socketio:
            return

        subnet_counts = defaultdict(int)
        subnet_details = defaultdict(list)

        for relay in relays:
            ip_parts = relay['ip'].split('.')
            if len(ip_parts) >= 2:
                subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                subnet_counts[subnet] += 1
                subnet_details[subnet].append({
                    'ip': relay['ip'],
                    'country': relay['country'],
                    'exit_probability': relay['exit_probability']
                })

        sorted_subnets = sorted(subnet_counts.items(),
                                key=lambda x: x[1], reverse=True)

        subnet_data = []
        for subnet, count in sorted_subnets:
            status = 'active' if subnet in self.active_subnets else 'available'
            limit = self.subnet_limits.get(subnet, 1)

            subnet_key = f"subnet_{subnet.replace('.', '_')}"
            running_instances = len([
                p for p in self.subnet_tor_processes.get(subnet_key, {}).values()
                if p and p.poll() is None
            ])

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
        """Start monitoring Tor relays and subnet information"""
        def monitor():
            while self.monitoring:
                relay_data = self.fetch_tor_relays()
                if relay_data:
                    relays = self.extract_relay_ips(relay_data)
                    self.current_relays = relays
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
        """Stop monitoring"""
        self.monitoring = False

    def _start_tor_instance(self, instance_id, subnet=None):
        """Start a single Tor instance"""
        tor_config_result = self.config_manager.create_tor_config(
            instance_id, subnet)
        tor_cmd = ['tor', '-f', tor_config_result['config_path']]

        logger.info(f"Starting Tor instance {instance_id} for subnet {subnet}")

        process = subprocess.Popen(
            tor_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        http_port = tor_config_result['http_port']

        if self.haproxy_manager.add_backend_instance(instance_id, http_port):
            logger.info(
                f"Started Tor instance {instance_id} on HTTP port {http_port}")
            return process
        else:
            logger.error(
                f"Tor instance {instance_id} failed to add to HAProxy")
            if process and process.poll() is None:
                process.terminate()
            return None

    def _start_subnet_tor_internal(self, subnet, instances_count=1):
        """Internal method to start Tor instances for a specific subnet (without lock)"""
        subnet_key = f"subnet_{subnet.replace('.', '_')}"

        if subnet_key not in self.subnet_tor_processes:
            self.subnet_tor_processes[subnet_key] = {}

        self.subnet_limits[subnet] = instances_count

        for i in range(instances_count):
            instance_id = self.get_next_available_id()
            process = self._start_tor_instance(instance_id, subnet)

            if process:
                self.subnet_tor_processes[subnet_key][instance_id] = process
            else:
                logger.error(
                    f"Failed to start Tor instance {instance_id} for subnet {subnet}")
                return False

        self.active_subnets.add(subnet)
        self.update_running_instances_count()
        logger.info(
            f"Started {instances_count} Tor instances for subnet {subnet}")
        return True

    def start_subnet_tor(self, subnet, instances_count=1):
        """Start Tor instances for a specific subnet"""
        with self._subnet_lock:
            return self._start_subnet_tor_internal(subnet, instances_count)

    def _stop_subnet_tor_internal(self, subnet):
        """Internal method to stop Tor instances for a specific subnet (without lock)"""
        subnet_key = f"subnet_{subnet.replace('.', '_')}"

        if subnet_key in self.subnet_tor_processes:
            # Create a copy of items to avoid "dictionary changed size during iteration"
            subnet_processes = list(self.subnet_tor_processes[subnet_key].items())
            for instance_id, process in subnet_processes:
                if process and process.poll() is None:
                    process.terminate()
                    logger.info(
                        f"Stopped Tor instance {instance_id} for subnet {subnet}")

                self.haproxy_manager.remove_backend_instance(instance_id)

            del self.subnet_tor_processes[subnet_key]

        self.active_subnets.discard(subnet)
        self.subnet_limits.pop(subnet, None)
        self.update_running_instances_count()
        logger.info(f"Stopped all instances for subnet {subnet}")
        return True

    def stop_subnet_tor(self, subnet):
        """Stop Tor instances for a specific subnet"""
        with self._subnet_lock:
            return self._stop_subnet_tor_internal(subnet)

    def restart_subnet_tor(self, subnet, instances_count=1):
        """Restart Tor instances for a subnet"""
        with self._subnet_lock:
            self._stop_subnet_tor_internal(subnet)
            time.sleep(2)
            return self._start_subnet_tor_internal(subnet, instances_count)

    def update_running_instances_count(self):
        """Update the count of running instances"""
        running_main = sum(
            1 for p in self.tor_processes.values() if p and p.poll() is None)

        # Create a copy to safely iterate in case of concurrent modification
        subnet_processes_copy = dict(self.subnet_tor_processes)
        running_subnet = sum(
            sum(1 for p in processes.values() if p and p.poll() is None)
            for processes in subnet_processes_copy.values()
        )
        self.stats['running_instances'] = running_main + running_subnet
        self.stats['tor_instances'] = running_main + running_subnet

    def get_next_available_id(self):
        """Get the next available instance ID"""
        current_id = self.next_instance_id
        self.next_instance_id += 1
        return current_id

    def get_subnet_running_instances(self, subnet):
        """Thread-safe method to get running instances count for a subnet"""
        # Create a copy for safe reading without blocking writes
        subnet_key = f"subnet_{subnet.replace('.', '_')}"
        subnet_processes_copy = dict(self.subnet_tor_processes)

        if subnet_key in subnet_processes_copy:
            return len([
                p for p in subnet_processes_copy[subnet_key].values()
                if p and p.poll() is None
            ])
        return 0
