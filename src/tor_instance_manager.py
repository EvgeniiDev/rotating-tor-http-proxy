import logging
import subprocess
import threading
import time
import requests
import os
import tempfile
from typing import List, Optional, Dict, Set
from datetime import datetime, timedelta
from utils import safe_stop_thread
from collections import defaultdict
import os, signal

logger = logging.getLogger(__name__)

TEST_URLS = [
    'https://httpbin.org/ip',
    'https://api.ipify.org?format=json',
    'https://icanhazip.com'
]

HTTP_OK = 200
REQUEST_TIMEOUT = 30
CONNECTION_TEST_TIMEOUT = 30


class TorInstanceManager:
    def __init__(self, port: int, exit_nodes: List[str], config_manager):
        self.port = port
        self.exit_nodes = exit_nodes
        self.config_manager = config_manager
        self.process = None
        self.config_file = None
        self.is_running = False
        self.failed_checks = 0
        self.max_failures = 3
        self.last_check = None
        self.current_exit_ip = None
        self.check_interval = 5
        self._lock = threading.RLock()
        self._monitor_thread = None
        self._shutdown_event = threading.Event()
        self.exit_node_activity: Dict[str, datetime] = {}
        self.suspicious_nodes: Set[str] = set()
        self.blacklisted_nodes: Set[str] = set()
        self.node_usage_count: Dict[str, int] = defaultdict(int)
        self.inactive_threshold = timedelta(minutes=60)
        self._log_exit_nodes()

    def _log_exit_nodes(self):
        log_path = os.path.expanduser(f"~/tor_exit_nodes.log")
        with open(log_path, "a") as f:
            f.write(f"port={self.port} ips={','.join(self.exit_nodes)}\n")
        
    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                logger.warning(f"Tor instance on port {self.port} is already running")
                return True
                
            try:
                if not self._create_config():
                    return False
                    
                if not self._start_tor_process():
                    return False
                    
                if not self._wait_for_startup():
                    self._cleanup()
                    return False
                    
                self.is_running = True
                self._start_monitoring()
                logger.info(f"Tor instance started successfully on port {self.port}")
                return True
                
            except Exception as e:
                logger.error(f"Failed to start Tor instance on port {self.port}: {e}")
                self._cleanup()
                return False
    
    def stop(self):
        with self._lock:
            if not self.is_running:
                return
                
            logger.info(f"Stopping Tor instance on port {self.port}")
            self._shutdown_event.set()
            self.is_running = False
            
            safe_stop_thread(self._monitor_thread, 5)
                
            self._stop_tor_process()
            self._cleanup()
            
    def restart(self) -> bool:
        self.stop()
        time.sleep(2)
        return self.start()
        
    def is_healthy(self) -> bool:
        for url in TEST_URLS:
            try:
                response = requests.get(
                    url,
                    proxies=self._get_proxies(),
                    timeout=REQUEST_TIMEOUT
                )
                
                if response.status_code == HTTP_OK:
                    if 'json' in response.headers.get('content-type', ''):
                        data = response.json()
                        if 'origin' in data:
                            self.current_exit_ip = data['origin'].strip()
                        elif 'ip' in data:
                            self.current_exit_ip = data['ip'].strip()
                    else:
                        self.current_exit_ip = response.text.strip()
                    
                    self.failed_checks = 0
                    self.last_check = datetime.now()
                    
                    if self.current_exit_ip:
                        self._report_active_exit_node(self.current_exit_ip)
                    
                    logger.debug(f"Port {self.port} health check passed with IP: {self.current_exit_ip}")
                    return True
                    
            except Exception as e:
                logger.debug(f"Port {self.port} health check failed for {url}: {e}")
                continue
                
        self.failed_checks += 1
        logger.warning(f"Port {self.port} health check failed after trying all URLs. Failed checks: {self.failed_checks}")
        return False
        
    def get_status(self) -> dict:
        exit_stats = self.get_exit_node_stats()
        return {
            'port': self.port,
            'is_running': self.is_running,
            'current_exit_ip': self.current_exit_ip,
            'exit_nodes_count': len(self.exit_nodes),
            'failed_checks': self.failed_checks,
            'last_check': self.last_check,
            'process_alive': self.process and self.process.poll() is None,
            'exit_node_monitoring': exit_stats
        }
        
    def _create_config(self) -> bool:
        try:
            temp_fd, self.config_file = tempfile.mkstemp(suffix='.torrc', prefix=f'tor_{self.port}_')
            
            with os.fdopen(temp_fd, 'w') as f:
                config_content = self.config_manager.get_tor_config_by_port(
                    self.port, 
                    self.exit_nodes
                )
                f.write(config_content)
                
            logger.debug(f"Created config file for port {self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to create config for port {self.port}: {e}")
            if self.config_file and os.path.exists(self.config_file):
                try:
                    os.unlink(self.config_file)
                except:
                    pass
                self.config_file = None
            return False
            
    def _start_tor_process(self) -> bool:
        try:
            cmd = ['tor', '-f', self.config_file]
            
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                preexec_fn=os.setsid
            )
            
            logger.debug(f"Started Tor process for port {self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Tor process for port {self.port}: {e}")
            return False
            
    def _stop_tor_process(self):
        if self.process:
            try:
                if self.process.poll() is None:
                    self.process.terminate()
                    
                    try:
                        self.process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        logger.warning(f"Force killing Tor process for port {self.port}")
                        self.process.kill()
                        self.process.wait()
                        
                logger.debug(f"Stopped Tor process for port {self.port}")
                
            except Exception as e:
                logger.error(f"Error stopping Tor process for port {self.port}: {e}")
            finally:
                self.process = None
    def reload_exit_nodes(self, new_exit_nodes: List[str]) -> bool:
        with self._lock:
            if not self.is_running or not self.process:
                return False
            self.exit_nodes = new_exit_nodes
            try:
                config_content = self.config_manager.get_tor_config_by_port(
                    self.port,
                    self.exit_nodes
                )
                with open(self.config_file, 'w') as f:
                    f.write(config_content)
                os.killpg(os.getpgid(self.process.pid), signal.SIGHUP)
                return True
            except Exception as e:
                logger.error(f"Failed to reload tor config for port {self.port}: {e}")
                return False
                
    def _wait_for_startup(self, timeout: int = 60) -> bool:
        start_time = time.time()
        logger.info(f"Waiting for Tor instance on port {self.port} to start up...")
        
        retry_count = 0
        max_retries_per_phase = 5
        
        while time.time() - start_time < timeout:
            if self.process and self.process.poll() is not None:
                logger.error(f"Tor process on port {self.port} died during startup")
                return False
            
            elapsed = time.time() - start_time
            
            if elapsed < 30:
                if retry_count < max_retries_per_phase:
                    if self._test_connection():
                        logger.info(f"Tor instance on port {self.port} is ready")
                        return True
                    retry_count += 1
                    logger.debug(f"Port {self.port} not ready yet, waiting... ({elapsed:.1f}s, try {retry_count})")
                    time.sleep(2)
                else:
                    logger.debug(f"Port {self.port} still not ready after {max_retries_per_phase} tries, waiting longer... ({elapsed:.1f}s)")
                    time.sleep(3)
                    retry_count = 0
            else:
                if self._test_connection():
                    logger.info(f"Tor instance on port {self.port} is ready")
                    return True
                logger.debug(f"Port {self.port} still bootstrapping... ({elapsed:.1f}s)")
                time.sleep(4)
            
        logger.warning(f"Tor instance on port {self.port} failed to start within {timeout}s")
        return False
        
    def _test_connection(self) -> bool:
        for url in TEST_URLS:
            try:
                response = requests.get(
                    url,
                    proxies=self._get_proxies(),
                    timeout=CONNECTION_TEST_TIMEOUT
                )
                if response.status_code == HTTP_OK:
                    logger.debug(f"Port {self.port} connection test passed with {url}")
                    return True
            except Exception as e:
                logger.debug(f"Port {self.port} connection test failed for {url}: {e}")
                continue
        
        return False
            
    def _get_proxies(self) -> dict:
        return {
            'http': f'socks5://127.0.0.1:{self.port}',
            'https': f'socks5://127.0.0.1:{self.port}'
        }
        
    def _start_monitoring(self):
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
            
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name=f"TorMonitor-{self.port}"
        )
        self._monitor_thread.daemon = True
        self._monitor_thread.start()
        
    def _monitor_loop(self):
        while not self._shutdown_event.is_set() and self.is_running:
            try:
                if not self.is_healthy() and self.failed_checks >= self.max_failures:
                    if not self.restart():
                        break
                
                self._check_inactive_exit_nodes()
                        
                self._shutdown_event.wait(self.check_interval)
                
            except Exception as e:
                logger.error(f"Error in monitoring loop for port {self.port}: {e}")
                time.sleep(5)
        
    def _cleanup(self):
        if self.config_file and os.path.exists(self.config_file):
            try:
                os.unlink(self.config_file)
            except Exception:
                pass
            finally:
                self.config_file = None

    def _report_active_exit_node(self, ip: str):
        with self._lock:
            self.exit_node_activity[ip] = datetime.now()
            self.node_usage_count[ip] += 1
            
            if ip in self.suspicious_nodes:
                self.suspicious_nodes.remove(ip)
                logger.info(f"Exit node {ip} on port {self.port} recovered from suspicious list")

    def get_inactive_exit_nodes(self) -> List[str]:
        with self._lock:
            current_time = datetime.now()
            inactive = []
            
            for ip, last_seen in self.exit_node_activity.items():
                if current_time - last_seen > self.inactive_threshold:
                    inactive.append(ip)
                    
            return inactive

    def get_suspicious_exit_nodes(self) -> List[str]:
        with self._lock:
            return list(self.suspicious_nodes)

    def get_blacklisted_exit_nodes(self) -> List[str]:
        with self._lock:
            return list(self.blacklisted_nodes)

    def blacklist_exit_node(self, ip: str):
        with self._lock:
            self.blacklisted_nodes.add(ip)
            self.suspicious_nodes.discard(ip)
            if ip in self.exit_node_activity:
                del self.exit_node_activity[ip]
            logger.warning(f"Exit node {ip} blacklisted on port {self.port}")

    def is_exit_node_healthy(self, ip: str) -> bool:
        with self._lock:
            return ip not in self.blacklisted_nodes and ip not in self.suspicious_nodes

    def get_exit_node_stats(self) -> dict:
        with self._lock:
            current_time = datetime.now()
            active_count = 0
            inactive_count = 0
            
            for ip, last_seen in self.exit_node_activity.items():
                if current_time - last_seen <= self.inactive_threshold:
                    active_count += 1
                else:
                    inactive_count += 1
                    
            return {
                'port': self.port,
                'total_tracked_nodes': len(self.exit_node_activity),
                'active_nodes': active_count,
                'inactive_nodes': inactive_count,
                'suspicious_nodes': len(self.suspicious_nodes),
                'blacklisted_nodes': len(self.blacklisted_nodes),
                'most_used_nodes': sorted(
                    self.node_usage_count.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:5]
            }

    def _check_inactive_exit_nodes(self):
        with self._lock:
            current_time = datetime.now()
            newly_suspicious = []
            
            for ip, last_seen in list(self.exit_node_activity.items()):
                if current_time - last_seen > self.inactive_threshold:
                    if ip not in self.suspicious_nodes and ip not in self.blacklisted_nodes:
                        self.suspicious_nodes.add(ip)
                        newly_suspicious.append(ip)
                        
            if newly_suspicious:
                logger.warning(f"Port {self.port}: Marked {len(newly_suspicious)} exit nodes as suspicious: {newly_suspicious[:3]}...")

    def get_healthy_exit_nodes(self) -> List[str]:
        with self._lock:
            return [node for node in self.exit_nodes 
                   if node not in self.blacklisted_nodes and node not in self.suspicious_nodes]
