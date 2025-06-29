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
        self.check_timeout = 10
        self.lock = threading.Lock()

    def check_health(self):
        with self.lock:
            try:
                proxies = {
                    'http': f'socks5://127.0.0.1:{self.port}',
                    'https': f'socks5://127.0.0.1:{self.port}'
                }

                response = requests.get(
                    'http://httpbin.org/ip',
                    proxies=proxies,
                    timeout=self.check_timeout
                )

                if response.status_code == 200:
                    self.failed_checks = 0
                    self.last_check = time.time()
                    return True
                else:
                    self.failed_checks += 1
                    logger.warning(
                        f"Health check failed for port {self.port}: HTTP {response.status_code}")

            except Exception as e:
                self.failed_checks += 1
                logger.warning(
                    f"Health check failed for port {self.port}: {e}")

            self.last_check = time.time()
            return False

    def quick_health_check(self):
        try:
            proxies = {
                'http': f'socks5://127.0.0.1:{self.port}',
                'https': f'socks5://127.0.0.1:{self.port}'
            }

            response = requests.get(
                'http://httpbin.org/ip',
                proxies=proxies,
                timeout=5
            )

            if response.status_code == 200:
                logger.debug(f"Quick health check passed for port {self.port}")
                return True
            else:
                logger.debug(
                    f"Quick health check failed for port {self.port}: HTTP {response.status_code}")

        except Exception as e:
            logger.debug(
                f"Quick health check failed for port {self.port}: {e}")

        return False

    def should_restart(self):
        with self.lock:
            return self.failed_checks >= self.max_failures

    def mark_restart(self):
        with self.lock:
            self.last_restart = time.time()
            self.failed_checks = 0

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
            health_monitor = TorInstanceHealth(port, exit_nodes)
            self.instance_health[port] = health_monitor
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
                    time.sleep(self.check_interval)
                    continue

                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    future_to_port = {
                        executor.submit(health_monitor.check_health): port
                        for port, health_monitor in instances_to_check
                    }

                    for future in concurrent.futures.as_completed(future_to_port, timeout=30):
                        port = future_to_port[future]
                        try:
                            is_healthy = future.result()
                            if not is_healthy:
                                with self._lock:
                                    if port in self.instance_health:
                                        health_monitor = self.instance_health[port]
                                        if health_monitor.should_restart():
                                            self._handle_restart(port, health_monitor)
                        except Exception as e:
                            logger.error(f"Health check failed for port {port}: {e}")

                time.sleep(self.check_interval)

            except Exception as e:
                logger.error(f"Error in health check worker: {e}")
                time.sleep(10)

        logger.info("Health check worker stopped")

    def _handle_restart(self, port, health_monitor):
        logger.warning(f"Port {port} failed {health_monitor.max_failures} health checks, restarting")

        try:
            health_monitor.mark_restart()

            if self.restart_callback:
                new_port = self.restart_callback(port)
                if new_port and new_port != port:
                    with self._lock:
                        del self.instance_health[port]
                        health_monitor.port = new_port
                        self.instance_health[new_port] = health_monitor

        except Exception as e:
            logger.error(f"Failed to restart instance on port {port}: {e}")

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
            health_stats = {}
            for port, health_monitor in self.instance_health.items():
                health_stats[port] = health_monitor.get_stats()

            return {
                'instance_health': health_stats,
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
