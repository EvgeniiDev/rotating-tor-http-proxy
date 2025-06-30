import time
import logging
import threading
import requests
import concurrent.futures
from typing import List

logger = logging.getLogger(__name__)


class TorInstanceHealth:
    def __init__(self, port, exit_nodes: List[str]):
        self.port = port
        self.exit_nodes = exit_nodes
        self.failed_checks = 0
        self.max_failures = 3
        self.last_check = None
        self.last_restart = None
        self.check_timeout = 30
        self.lock = threading.Lock()

    def _get_proxies(self):
        return {
            'http': f'socks5://127.0.0.1:{self.port}',
            'https': f'socks5://127.0.0.1:{self.port}'
        }

    def _perform_health_check(self, timeout=None, quick=False):
        try:
            response = requests.get(
                'http://httpbin.org/ip',
                proxies=self._get_proxies(),
                timeout=timeout or (5 if quick else self.check_timeout)
            )
            
            success = response.status_code == 200
            log_level = logger.debug if quick else logger.warning
            
            if not success:
                log_level(f"Health check failed for port {self.port}: HTTP {response.status_code}")
            elif quick:
                logger.debug(f"Quick health check passed for port {self.port}")
                
            return success
            
        except Exception as e:
            log_level = logger.debug if quick else logger.warning
            log_level(f"Health check failed for port {self.port}: {e}")
            return False

    def check_health(self):
        with self.lock:
            success = self._perform_health_check()
            
            if success:
                self.failed_checks = 0
            else:
                self.failed_checks += 1
                
            self.last_check = time.time()
            return success

    def quick_health_check(self):
        return self._perform_health_check(quick=True)

    def should_restart(self):
        with self.lock:
            return self.failed_checks >= self.max_failures

    def mark_restart(self):
        with self.lock:
            self.last_restart = time.time()
            self.failed_checks = 0
            logger.info(f"Port {self.port} restart marked, failed checks reset to 0")

    def get_stats(self):
        with self.lock:
            return {
                'port': self.port,
                'exit_nodes_count': len(self.exit_nodes),
                'failed_checks': self.failed_checks,
                'last_check': self.last_check,
                'last_restart': self.last_restart
            }


class TorHealthMonitor:
    def __init__(self, restart_callback, check_interval=15, get_available_exit_nodes_callback=None):
        self.instance_health = {}
        self.health_check_running = True
        self.restart_callback = restart_callback
        self.get_available_exit_nodes_callback = get_available_exit_nodes_callback
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self._health_thread = None

    def add_instance(self, port, exit_nodes: List[str]):
        with self._lock:
            self.instance_health[port] = TorInstanceHealth(port, exit_nodes)
            logger.info(f"Added health monitoring for port {port} with {len(exit_nodes)} exit nodes")

    def remove_instance(self, port):
        with self._lock:
            if port in self.instance_health:
                del self.instance_health[port]
                logger.info(f"Removed health monitoring for port {port}")

    def _health_check_worker(self):
        logger.info("Starting health check worker")
        while self.health_check_running:
            try:
                with self._lock:
                    instances_to_check = list(self.instance_health.items())

                if not instances_to_check:
                    logger.debug("No Tor instances to monitor, waiting...")
                    time.sleep(self.check_interval)
                    continue

                logger.debug(f"Health check cycle starting for {len(instances_to_check)} instances")
                cycle_start_time = time.time()
                
                self._check_instances_health(instances_to_check)
                
                cycle_duration = time.time() - cycle_start_time
                logger.debug(f"Health check cycle completed in {cycle_duration:.2f}s")
                
                time.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"Error in health check worker: {e}")
                time.sleep(10)

        logger.info("Health check worker stopped")

    def _check_instances_health(self, instances_to_check):
        logger.debug(f"Checking health of {len(instances_to_check)} Tor instances")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future_to_port = {
                executor.submit(health_monitor.check_health): port
                for port, health_monitor in instances_to_check
            }

            failed_instances = []
            for future in concurrent.futures.as_completed(future_to_port, timeout=30):
                port = future_to_port[future]
                try:
                    if not future.result():
                        failed_instances.append(port)
                        with self._lock:
                            if port in self.instance_health:
                                health_monitor = self.instance_health[port]
                                logger.debug(f"Port {port} health check failed ({health_monitor.failed_checks}/{health_monitor.max_failures} failures)")
                                if health_monitor.should_restart():
                                    logger.warning(f"Port {port} reached maximum failures, triggering restart")
                                    self._handle_restart(port, health_monitor)
                except Exception as e:
                    logger.error(f"Health check failed for port {port}: {e}")
            
            if failed_instances:
                logger.info(f"Health check completed: {len(failed_instances)} instances failed out of {len(instances_to_check)}")
            else:
                logger.debug(f"Health check completed: all {len(instances_to_check)} instances healthy")

    def _handle_restart(self, port, health_monitor):
        logger.warning(f"Port {port} failed {health_monitor.max_failures} consecutive health checks")
        logger.info(f"Initiating Tor process restart for port {port}")

        try:
            restart_start_time = time.time()
            health_monitor.mark_restart()

            if self.restart_callback:
                logger.info(f"Calling restart callback for port {port}")
                new_port = self.restart_callback(port)
                
                restart_duration = time.time() - restart_start_time
                
                if new_port and new_port != port:
                    logger.info(f"Tor process successfully restarted: port {port} -> {new_port} (took {restart_duration:.2f}s)")
                    with self._lock:
                        del self.instance_health[port]
                        health_monitor.port = new_port
                        self.instance_health[new_port] = health_monitor
                    logger.info(f"Health monitoring transferred from port {port} to port {new_port}")
                elif new_port == port:
                    logger.info(f"Tor process restarted on same port {port} (took {restart_duration:.2f}s)")
                else:
                    logger.error(f"Restart callback returned invalid port: {new_port} for original port {port}")
            else:
                logger.error(f"No restart callback available for port {port}")

        except Exception as e:
            logger.error(f"Exception during Tor process restart for port {port}: {e}")

    def start(self):
        if self._health_thread and self._health_thread.is_alive():
            logger.info("Health monitoring already running")
            return

        self.health_check_running = True
        self._health_thread = threading.Thread(target=self._health_check_worker)
        self._health_thread.daemon = True
        self._health_thread.start()
        logger.info("Started health monitoring")

    def stop(self):
        self.health_check_running = False
        if self._health_thread:
            self._health_thread.join(timeout=5)

    def get_stats(self):
        with self._lock:
            return {
                'instance_health': {port: monitor.get_stats() for port, monitor in self.instance_health.items()},
                'total_instances': len(self.instance_health)
            }

    def clear(self):
        with self._lock:
            self.instance_health.clear()

    def is_instance_ready(self, port):
        with self._lock:
            if port not in self.instance_health:
                return False

            health_monitor = self.instance_health[port]
            if health_monitor.last_check is None:
                try:
                    return health_monitor.check_health()
                except Exception as e:
                    logger.warning(f"Error checking readiness for port {port}: {e}")
                    return False

            return health_monitor.failed_checks == 0
