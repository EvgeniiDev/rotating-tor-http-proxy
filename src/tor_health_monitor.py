import time
import logging
import threading
import requests

logger = logging.getLogger(__name__)


class TorInstanceHealth:
    def __init__(self, port, subnet):
        self.port = port
        self.subnet = subnet
        self.failed_checks = 0
        self.max_failures = 3
        self.restart_count = 0
        self.last_check = None
        self.last_restart = None
        self.check_timeout = 30
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
                    logger.warning(f"Health check failed for port {self.port}: HTTP {response.status_code}")
                    
            except Exception as e:
                self.failed_checks += 1
                logger.warning(f"Health check failed for port {self.port}: {e}")
            
            self.last_check = time.time()
            return False
    
    def should_restart(self):
        with self.lock:
            return self.failed_checks >= self.max_failures
    
    def mark_restart(self):
        with self.lock:
            self.restart_count += 1
            self.last_restart = time.time()
            self.failed_checks = 0
    
    def get_stats(self):
        with self.lock:
            return {
                'port': self.port,
                'subnet': self.subnet,
                'failed_checks': self.failed_checks,
                'restart_count': self.restart_count,
                'last_check': self.last_check,
                'last_restart': self.last_restart
            }


class TorHealthMonitor:
    def __init__(self, restart_callback):
        self.instance_health = {}
        self.health_check_running = True
        self.restart_callback = restart_callback
        self.subnet_restart_counts = {}
        self._lock = threading.Lock()
    
    def add_instance(self, port, subnet):
        with self._lock:
            health_monitor = TorInstanceHealth(port, subnet)
            self.instance_health[port] = health_monitor
            logger.info(f"Added health monitoring for port {port}")
    
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
                
                for port, health_monitor in instances_to_check:
                    if not self.health_check_running:
                        break
                    
                    if health_monitor.check_health():
                        continue
                    
                    if health_monitor.should_restart():
                        logger.warning(f"Port {port} failed {health_monitor.max_failures} health checks, restarting")
                        
                        try:
                            health_monitor.mark_restart()
                            subnet = health_monitor.subnet
                            
                            with self._lock:
                                if subnet not in self.subnet_restart_counts:
                                    self.subnet_restart_counts[subnet] = 0
                                self.subnet_restart_counts[subnet] += 1
                            
                            if self.restart_callback:
                                new_port = self.restart_callback(port, subnet)
                                if new_port and new_port != port:
                                    with self._lock:
                                        del self.instance_health[port]
                                        health_monitor.port = new_port
                                        self.instance_health[new_port] = health_monitor
                                
                        except Exception as e:
                            logger.error(f"Failed to restart instance on port {port}: {e}")
                
                time.sleep(60)
                
            except Exception as e:
                logger.error(f"Error in health check worker: {e}")
                time.sleep(10)
        
        logger.info("Health check worker stopped")
    
    def start(self):
        if hasattr(self, '_health_thread') and self._health_thread.is_alive():
            logger.info("Health monitoring already running")
            return
        
        self.health_check_running = True
        self._health_thread = threading.Thread(target=self._health_check_worker)
        self._health_thread.daemon = True
        self._health_thread.start()
        logger.info("Started health monitoring")
    
    def stop(self):
        self.health_check_running = False
        if hasattr(self, '_health_thread'):
            self._health_thread.join(timeout=5)
    
    def get_stats(self):
        with self._lock:
            health_stats = {}
            for port, health_monitor in self.instance_health.items():
                health_stats[port] = health_monitor.get_stats()
            
            return {
                'instance_health': health_stats,
                'subnet_restart_counts': dict(self.subnet_restart_counts),
                'total_restarts': sum(self.subnet_restart_counts.values())
            }
    
    def clear(self):
        with self._lock:
            self.instance_health.clear()
            self.subnet_restart_counts.clear()
