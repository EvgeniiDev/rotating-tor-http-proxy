import time
import logging
import threading
import requests
import concurrent.futures

logger = logging.getLogger(__name__)


class TorInstanceHealth:
    def __init__(self, port, subnet):
        self.port = port
        self.subnet = subnet
        self.failed_checks = 0
        self.max_failures = 3
        self.restart_count = 0
        self.max_restarts = 3
        self.last_check = None
        self.last_restart = None
        self.check_timeout = 15
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
    
    def should_change_subnet(self):
        with self.lock:
            return self.restart_count >= self.max_restarts
    
    def mark_restart(self):
        with self.lock:
            self.restart_count += 1
            self.last_restart = time.time()
            self.failed_checks = 0
    
    def change_subnet(self, new_subnet):
        with self.lock:
            old_subnet = self.subnet
            self.subnet = new_subnet
            self.restart_count = 0
            self.failed_checks = 0
            logger.info(f"Changed subnet from {old_subnet} to {new_subnet} for port {self.port}")
    
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
    def __init__(self, restart_callback, check_interval=30, get_available_subnets_callback=None):
        self.instance_health = {}
        self.health_check_running = True
        self.restart_callback = restart_callback
        self.get_available_subnets_callback = get_available_subnets_callback
        self.subnet_restart_counts = {}
        self.check_interval = check_interval
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
                
                if not instances_to_check:
                    time.sleep(self.check_interval)
                    continue
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    future_to_port = {
                        executor.submit(health_monitor.check_health): port
                        for port, health_monitor in instances_to_check
                    }
                    
                    try:
                        for future in concurrent.futures.as_completed(future_to_port, timeout=20):
                            port = future_to_port[future]
                            
                            if not self.health_check_running:
                                break
                            
                            try:
                                health_ok = future.result()
                                if health_ok:
                                    continue
                                
                                with self._lock:
                                    if port not in self.instance_health:
                                        continue
                                    health_monitor = self.instance_health[port]
                                
                                if health_monitor.should_restart():
                                    self._handle_restart(port, health_monitor)
                                    
                            except Exception as e:
                                logger.error(f"Health check failed for port {port}: {e}")
                                
                    except concurrent.futures.TimeoutError:
                        logger.warning("Some health checks timed out, continuing...")
                
                time.sleep(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in health check worker: {e}")
                time.sleep(10)
        
        logger.info("Health check worker stopped")
    
    def _handle_restart(self, port, health_monitor):
        current_subnet = health_monitor.subnet
        
        if health_monitor.should_change_subnet():
            logger.warning(f"Port {port} failed {health_monitor.max_restarts} restarts, changing subnet")
            
            if self.get_available_subnets_callback:
                try:
                    used_subnets = {hm.subnet for hm in self.instance_health.values()}
                    available_subnets = self.get_available_subnets_callback(1, exclude=used_subnets)
                    
                    if available_subnets:
                        new_subnet = available_subnets[0]
                        health_monitor.change_subnet(new_subnet)
                        
                        if self.restart_callback:
                            new_port = self.restart_callback(port, new_subnet)
                            if new_port and new_port != port:
                                with self._lock:
                                    del self.instance_health[port]
                                    health_monitor.port = new_port
                                    self.instance_health[new_port] = health_monitor
                    else:
                        logger.error(f"No available subnets for changing subnet for port {port}")
                except Exception as e:
                    logger.error(f"Failed to change subnet for port {port}: {e}")
            else:
                logger.warning(f"No subnet provider callback available for port {port}")
        
        logger.warning(f"Port {port} failed {health_monitor.max_failures} health checks, restarting")
        
        try:
            health_monitor.mark_restart()
            
            with self._lock:
                if current_subnet not in self.subnet_restart_counts:
                    self.subnet_restart_counts[current_subnet] = 0
                self.subnet_restart_counts[current_subnet] += 1
            
            if self.restart_callback:
                new_port = self.restart_callback(port, current_subnet)
                if new_port and new_port != port:
                    with self._lock:
                        del self.instance_health[port]
                        health_monitor.port = new_port
                        self.instance_health[new_port] = health_monitor
                        
        except Exception as e:
            logger.error(f"Failed to restart instance on port {port}: {e}")
    
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
