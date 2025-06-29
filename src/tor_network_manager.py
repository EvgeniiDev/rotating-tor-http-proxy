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
        self.process_manager = TorProcessManager(
            self.config_manager, load_balancer)
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
        available_subnets = self.get_available_subnets(count * 3)

        if not available_subnets:
            relay_data = self.fetch_tor_relays()
            if relay_data:
                self.extract_relay_ips(relay_data)
                available_subnets = self.get_available_subnets(count * 3)

        if not available_subnets:
            logger.warning("No subnets available for Tor instances")
            return

        logger.info(f"Available subnets: {len(available_subnets)}")

        batch_size = 20
        total_started = 0
        subnet_index = 0
        failed_subnets = set()
        pending_instances = []

        for batch_start in range(0, count, batch_size):
            batch_end = min(batch_start + batch_size, count)
            instances_needed = batch_end - batch_start

            logger.info(
                f"Starting batch {batch_start//batch_size + 1}: instances {batch_start + 1}-{batch_end}")

            batch_ports = []
            for i in range(instances_needed):
                port = None
                attempts = 0
                max_attempts = 3

                while not port and attempts < max_attempts and subnet_index < len(available_subnets):
                    current_subnet = available_subnets[subnet_index]

                    if current_subnet in failed_subnets:
                        subnet_index += 1
                        continue

                    try:
                        temp_port = self.process_manager.start_tor_instance(
                            current_subnet)
                        if temp_port:
                            self.health_monitor.add_instance(
                                temp_port, current_subnet)

                            logger.info(
                                f"Started Tor instance on port {temp_port} with subnet {current_subnet}, testing...")
                            health_result = self._check_tor_instance_health_progressive(
                                temp_port, current_subnet)

                            if health_result == 'ready':
                                port = temp_port
                                batch_ports.append(port)
                                total_started += 1
                                logger.info(
                                    f"Successfully started and verified Tor instance on port {port} with subnet {current_subnet}")
                                subnet_index += 1
                            elif health_result == 'pending':
                                pending_instances.append({
                                    'port': temp_port,
                                    'subnet': current_subnet,
                                    'start_time': time.time(),
                                    'attempts': 1
                                })
                                logger.info(
                                    f"Tor instance on port {temp_port} with subnet {current_subnet} needs more time, added to pending list")
                                subnet_index += 1
                                # Don't break, continue with next subnet
                            else:
                                # Don't change subnet on first failure
                                if attempts < max_attempts - 1:
                                    logger.warning(
                                        f"Tor instance on port {temp_port} failed health check, keeping same subnet and retrying")
                                    self.health_monitor.remove_instance(
                                        temp_port)
                                    self.process_manager.stop_tor_instance(
                                        temp_port)
                                    attempts += 1
                                else:
                                    logger.warning(
                                        f"Tor instance on port {temp_port} failed health check after {attempts + 1} attempts, trying next subnet")
                                    self.health_monitor.remove_instance(
                                        temp_port)
                                    self.process_manager.stop_tor_instance(
                                        temp_port)
                                    failed_subnets.add(current_subnet)
                                    subnet_index += 1
                                    attempts = 0
                        else:
                            logger.warning(
                                f"Failed to start Tor instance with subnet {current_subnet}, trying next subnet")
                            failed_subnets.add(current_subnet)
                            subnet_index += 1
                            attempts += 1
                    except Exception as e:
                        logger.error(
                            f"Error starting Tor instance with subnet {current_subnet}: {e}")
                        failed_subnets.add(current_subnet)
                        subnet_index += 1
                        attempts += 1

                if not port:
                    logger.warning(
                        f"Failed to start instance {batch_start + i + 1} after trying {attempts} subnets")

            if batch_end < count and batch_ports:
                logger.info(
                    f"Batch {batch_start//batch_size + 1} completed. Waiting for instances to be ready...")
                self._wait_for_batch_ready(batch_ports)

        if pending_instances:
            logger.info(
                f"Processing {len(pending_instances)} pending instances...")
            verified_pending = self._process_pending_instances(
                pending_instances, available_subnets[subnet_index:], failed_subnets)
            total_started += verified_pending

        if failed_subnets:
            logger.info(f"Failed subnets: {list(failed_subnets)}")

        logger.info(
            f"Auto-start completed: {total_started}/{count} instances started successfully")

    def _wait_for_batch_ready(self, batch_ports, max_wait_time=120, check_interval=2):
        start_time = time.time()

        while time.time() - start_time < max_wait_time:
            ready_ports = []
            for port in batch_ports:
                if self.health_monitor.is_instance_ready(port):
                    ready_ports.append(port)

            if len(ready_ports) == len(batch_ports):
                logger.info(
                    f"All {len(batch_ports)} instances in batch are ready")
                return True

            if len(ready_ports) > 0:
                logger.info(
                    f"{len(ready_ports)}/{len(batch_ports)} instances ready, waiting for remaining...")

            time.sleep(check_interval)

        ready_count = sum(
            1 for port in batch_ports if self.health_monitor.is_instance_ready(port))
        logger.warning(
            f"Batch readiness timeout: {ready_count}/{len(batch_ports)} instances ready after {max_wait_time}s")
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
            running_instances = self.process_manager.get_subnet_running_instances(
                subnet)
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
        sorted_subnets = sorted(subnet_counts.items(),
                                key=lambda x: x[1], reverse=True)

        subnet_data = []
        for subnet, count in sorted_subnets:
            status = 'active' if subnet in self.active_subnets else 'available'
            limit = self.subnet_limits.get(subnet, 1)
            running_instances = self.process_manager.get_subnet_running_instances(
                subnet)

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
        max_attempts = 3
        current_subnet = subnet
        used_subnets = set()

        for attempt in range(max_attempts):
            try:
                success, started_ports = self.process_manager.start_subnet_instances(
                    current_subnet, instances_count)

                if success and started_ports:
                    verified_ports = []

                    for port in started_ports:
                        self.health_monitor.add_instance(port, current_subnet)

                        if self._verify_tor_instance_health(port, current_subnet):
                            verified_ports.append(port)
                            logger.info(
                                f"Tor instance on port {port} with subnet {current_subnet} verified")
                        else:
                            logger.warning(
                                f"Tor instance on port {port} with subnet {current_subnet} failed health check")
                            self.health_monitor.remove_instance(port)
                            self.process_manager.stop_tor_instance(port)

                    if verified_ports:
                        self.active_subnets.add(current_subnet)
                        self.subnet_limits[current_subnet] = len(
                            verified_ports)
                        self.update_running_instances_count()

                        if current_subnet != subnet:
                            logger.info(
                                f"Started {len(verified_ports)} verified Tor instances for subnet {current_subnet} (fallback from {subnet})")
                        else:
                            logger.info(
                                f"Started {len(verified_ports)} verified Tor instances for subnet {current_subnet}")

                        return True
                    else:
                        logger.warning(
                            f"No Tor instances for subnet {current_subnet} passed health check")
                        used_subnets.add(current_subnet)
                else:
                    used_subnets.add(current_subnet)
                    logger.warning(
                        f"Failed to start Tor instances for subnet {current_subnet} (attempt {attempt + 1})")

                if attempt < max_attempts - 1:
                    alternative_subnets = self._get_available_subnets_for_health_monitor(
                        5, used_subnets)
                    if alternative_subnets:
                        current_subnet = alternative_subnets[0]
                        logger.info(
                            f"Trying alternative subnet: {current_subnet}")
                    else:
                        logger.warning("No alternative subnets available")
                        break

            except Exception as e:
                used_subnets.add(current_subnet)
                logger.error(
                    f"Error starting Tor instances for subnet {current_subnet}: {e}")

                if attempt < max_attempts - 1:
                    alternative_subnets = self._get_available_subnets_for_health_monitor(
                        5, used_subnets)
                    if alternative_subnets:
                        current_subnet = alternative_subnets[0]
                        logger.info(
                            f"Trying alternative subnet after error: {current_subnet}")
                    else:
                        logger.warning(
                            "No alternative subnets available after error")
                        break

        logger.error(
            f"Failed to start verified Tor instances after {max_attempts} attempts with different subnets")
        return False

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
        exclude_set = set(exclude)

        # Combine excluded subnets and currently active subnets
        all_used_subnets = exclude_set.union(self.active_subnets)

        # Also add all subnets currently used by running instances
        with self.process_manager._lock:
            all_used_subnets.update(
                set(self.process_manager.port_subnets.values()))

        # Get more subnets than needed to have flexibility in filtering
        available_subnets = self.get_available_subnets(count * 3)

        filtered_subnets = []
        for subnet in available_subnets:
            if subnet not in all_used_subnets:
                filtered_subnets.append(subnet)
                if len(filtered_subnets) >= count:
                    break

        # If we couldn't find enough unique subnets, try refreshing relay data
        if len(filtered_subnets) < count:
            relay_data = self.fetch_tor_relays()
            if relay_data:
                self.extract_relay_ips(relay_data)
                available_subnets = self.get_available_subnets(count * 3)

                for subnet in available_subnets:
                    if subnet not in all_used_subnets and subnet not in filtered_subnets:
                        filtered_subnets.append(subnet)
                        if len(filtered_subnets) >= count:
                            break

        if filtered_subnets:
            logger.info(
                f"Found {len(filtered_subnets)} unique available subnets")
        else:
            logger.warning("Could not find any unique available subnets")

        return filtered_subnets[:count]

    def _restart_tor_instance_by_port(self, port, subnet):
        max_attempts = 3
        current_subnet = subnet
        # Start by marking the current subnet as used
        used_subnets = set([subnet])

        for attempt in range(max_attempts):
            try:
                # Ensure we're not reusing a subnet that's already in use elsewhere
                with self.process_manager._lock:
                    active_subnets = set(
                        self.process_manager.port_subnets.values())

                if current_subnet in active_subnets and current_subnet != subnet:
                    logger.warning(
                        f"Subnet {current_subnet} is already in use by another instance")
                    used_subnets.add(current_subnet)

                    # Get a truly unique subnet
                    alternative_subnets = self._get_available_subnets_for_health_monitor(
                        1, exclude=active_subnets.union(used_subnets))

                    if alternative_subnets:
                        current_subnet = alternative_subnets[0]
                        logger.info(
                            f"Switched to truly unique subnet: {current_subnet}")
                    else:
                        logger.warning(
                            "No unique subnets available, trying with current subnet")

                result = self.process_manager.restart_instance_by_port(
                    port, current_subnet)

                if result:
                    self.health_monitor.remove_instance(port)
                    self.health_monitor.add_instance(result, current_subnet)

                    if self._verify_tor_instance_health(result, current_subnet):
                        if current_subnet != subnet:
                            logger.info(
                                f"Successfully restarted and verified Tor instance on port {result} with alternative subnet {current_subnet} (original: {subnet})")
                        else:
                            logger.info(
                                f"Successfully restarted and verified Tor instance on port {result} with subnet {current_subnet}")
                        return result
                    else:
                        logger.warning(
                            f"Restarted Tor instance on port {result} with subnet {current_subnet} failed health check")
                        self.health_monitor.remove_instance(result)
                        self.process_manager.stop_tor_instance(result)
                        used_subnets.add(current_subnet)
                else:
                    used_subnets.add(current_subnet)
                    logger.warning(
                        f"Failed to restart Tor instance with subnet {current_subnet} (attempt {attempt + 1})")

                if attempt < max_attempts - 1:
                    # Get the current active subnets again to ensure we have the latest info
                    with self.process_manager._lock:
                        active_subnets = set(
                            self.process_manager.port_subnets.values())

                    # Exclude all used and active subnets to ensure uniqueness
                    all_excluded = used_subnets.union(
                        active_subnets).union(self.active_subnets)

                    alternative_subnets = self._get_available_subnets_for_health_monitor(
                        5, all_excluded)

                    if alternative_subnets:
                        current_subnet = alternative_subnets[0]
                        logger.info(
                            f"Trying alternative unique subnet for restart: {current_subnet}")
                    else:
                        logger.warning(
                            "No alternative unique subnets available for restart")
                        break

            except Exception as e:
                used_subnets.add(current_subnet)
                logger.error(
                    f"Error restarting Tor instance with subnet {current_subnet}: {e}")

                if attempt < max_attempts - 1:
                    # Similar approach for error recovery
                    with self.process_manager._lock:
                        active_subnets = set(
                            self.process_manager.port_subnets.values())

                    all_excluded = used_subnets.union(
                        active_subnets).union(self.active_subnets)

                    alternative_subnets = self._get_available_subnets_for_health_monitor(
                        5, all_excluded)

                    if alternative_subnets:
                        current_subnet = alternative_subnets[0]
                        logger.info(
                            f"Trying alternative unique subnet after restart error: {current_subnet}")
                    else:
                        logger.warning(
                            "No alternative unique subnets available after restart error")
                        break

        logger.error(
            f"Failed to restart and verify Tor instance after {max_attempts} attempts with different subnets")
        return False

    def get_health_stats(self):
        return self.health_monitor.get_stats()

    def _verify_tor_instance_health(self, port, subnet, max_wait_time=30, check_interval=2):
        start_time = time.time()
        check_points = [5, 10, 15, 20, 25]

        logger.info(
            f"Verifying health of Tor instance on port {port} with subnet {subnet}")

        while time.time() - start_time < max_wait_time:
            elapsed = time.time() - start_time

            if self.health_monitor.quick_instance_check(port):
                logger.info(
                    f"Tor instance on port {port} with subnet {subnet} passed health check after {int(elapsed)}s")
                return True

            # Log progress at specific checkpoints
            for checkpoint in check_points:
                if elapsed >= checkpoint and elapsed < checkpoint + check_interval:
                    logger.debug(
                        f"Waiting for Tor instance on port {port} to be ready... ({int(elapsed)}s/{max_wait_time}s)")
                    break

            time.sleep(check_interval)

        logger.warning(
            f"Tor instance on port {port} with subnet {subnet} failed health check after {max_wait_time}s")
        return False

    def _check_tor_instance_health_progressive(self, port, subnet):
        check_intervals = [5, 5, 5]

        for i, interval in enumerate(check_intervals):
            logger.debug(
                f"Health check attempt {i+1}/3 for port {port}, waiting {interval}s...")
            time.sleep(interval)

            if self.health_monitor.quick_instance_check(port):
                logger.info(
                    f"Tor instance on port {port} with subnet {subnet} passed health check on attempt {i+1}")
                return 'ready'

        logger.debug(
            f"Tor instance on port {port} with subnet {subnet} needs more time after 15s")
        return 'pending'

    def _process_pending_instances(self, pending_instances, remaining_subnets, failed_subnets):
        logger.info(
            f"Processing {len(pending_instances)} pending instances...")
        verified_count = 0
        still_pending_instances = []

        for instance in pending_instances:
            port = instance['port']
            subnet = instance['subnet']
            elapsed = time.time() - instance['start_time']

            logger.info(
                f"Checking pending instance on port {port} (elapsed: {int(elapsed)}s)")

            if self.health_monitor.quick_instance_check(port):
                logger.info(
                    f"Pending Tor instance on port {port} with subnet {subnet} is now ready")
                verified_count += 1
            else:
                check_intervals = [5, 5, 5]
                ready = False

                for i, interval in enumerate(check_intervals):
                    logger.debug(
                        f"Additional check attempt {i+1}/3 for port {port}, waiting {interval}s...")
                    time.sleep(interval)

                    if self.health_monitor.quick_instance_check(port):
                        logger.info(
                            f"Pending Tor instance on port {port} with subnet {subnet} is now ready after extra check")
                        verified_count += 1
                        ready = True
                        break

                if not ready:
                    logger.warning(
                        f"Pending Tor instance on port {port} with subnet {subnet} still not ready after additional checks")
                    still_pending_instances.append(instance)

        if still_pending_instances:
            logger.info(
                f"Restarting {len(still_pending_instances)} failed instances with same subnets first...")
            subnet_index = 0

            for instance in still_pending_instances:
                old_port = instance['port']
                old_subnet = instance['subnet']

                self.health_monitor.remove_instance(old_port)
                self.process_manager.stop_tor_instance(old_port)

                new_port = None
                attempts = 0
                max_attempts = 3

                # First try with the same subnet
                try:
                    logger.info(
                        f"First trying to restart with the same subnet: {old_subnet}")
                    temp_port = self.process_manager.start_tor_instance(
                        old_subnet)
                    if temp_port:
                        self.health_monitor.add_instance(temp_port, old_subnet)

                        # Progressive check with 3 attempts of 5 seconds each
                        health_result = self._check_tor_instance_health_progressive(
                            temp_port, old_subnet)

                        if health_result == 'ready':
                            new_port = temp_port
                            verified_count += 1
                            logger.info(
                                f"Successfully restarted failed instance on port {new_port} with the same subnet {old_subnet}")
                        else:
                            logger.warning(
                                f"Failed to verify restarted instance with original subnet {old_subnet}, will try alternative subnets")
                            self.health_monitor.remove_instance(temp_port)
                            self.process_manager.stop_tor_instance(temp_port)
                except Exception as e:
                    logger.error(
                        f"Error restarting instance with original subnet {old_subnet}: {e}")

                # Only if original subnet failed, try with alternative subnets
                while not new_port and attempts < max_attempts and subnet_index < len(remaining_subnets):
                    current_subnet = remaining_subnets[subnet_index]

                    if current_subnet in failed_subnets or current_subnet == old_subnet:
                        subnet_index += 1
                        continue

                    try:
                        temp_port = self.process_manager.start_tor_instance(
                            current_subnet)
                        if temp_port:
                            self.health_monitor.add_instance(
                                temp_port, current_subnet)

                            health_result = self._check_tor_instance_health_progressive(
                                temp_port, current_subnet)

                            if health_result == 'ready':
                                new_port = temp_port
                                verified_count += 1
                                logger.info(
                                    f"Successfully restarted failed instance on port {new_port} with alternative subnet {current_subnet}")
                                subnet_index += 1
                            else:
                                logger.warning(
                                    f"Failed to verify restarted instance on port {temp_port} with alternative subnet {current_subnet}")
                                self.health_monitor.remove_instance(temp_port)
                                self.process_manager.stop_tor_instance(
                                    temp_port)
                                failed_subnets.add(current_subnet)
                                subnet_index += 1
                                attempts += 1
                        else:
                            logger.warning(
                                f"Failed to restart instance with alternative subnet {current_subnet}")
                            failed_subnets.add(current_subnet)
                            subnet_index += 1
                            attempts += 1
                    except Exception as e:
                        logger.error(
                            f"Error restarting instance with alternative subnet {current_subnet}: {e}")
                        failed_subnets.add(current_subnet)
                        subnet_index += 1
                        attempts += 1

                if not new_port:
                    logger.warning(
                        f"Failed to restart instance from port {old_port} after trying alternative subnets")

        return verified_count
