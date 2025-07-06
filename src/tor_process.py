import subprocess
import tempfile
import os
import time
from typing import List, Optional, Dict, Set
from datetime import datetime, timedelta
import signal
import requests

TEST_URLS = [
    'https://httpbin.org/ip',
    'https://api.ipify.org?format=json',
    'https://icanhazip.com'
]

REQUEST_TIMEOUT = 30


class TorProcess:
    __slots__ = (
        'port', 'exit_nodes', 'process', 'config_file', 'is_running',
        'failed_checks', 'max_failures', 'last_check', 'current_exit_ip',
        'exit_node_activity', 'suspicious_nodes', 'blacklisted_nodes',
        'node_usage_count', 'inactive_threshold'
    )
    
    def __init__(self, port: int, exit_nodes: List[str]):
        self.port = port
        self.exit_nodes = exit_nodes
        self.process = None
        self.config_file = None
        self.is_running = False
        self.failed_checks = 0
        self.max_failures = 3
        self.last_check = None
        self.current_exit_ip = None
        self.exit_node_activity: Dict[str, datetime] = {}
        self.suspicious_nodes: Set[str] = set()
        self.blacklisted_nodes: Set[str] = set()
        self.node_usage_count: Dict[str, int] = {}
        self.inactive_threshold = timedelta(minutes=60)

    def _make_request(self, url: str) -> Optional[requests.Response]:
        try:
            response = requests.get(
                url,
                proxies=self.get_proxies(),
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()
            return response
        except requests.RequestException:
            return None

    def create_config(self, config_manager) -> bool:
        temp_fd, self.config_file = tempfile.mkstemp(suffix='.torrc', prefix=f'tor_{self.port}_')
        
        with os.fdopen(temp_fd, 'w') as f:
            config_content = config_manager.get_tor_config_by_port(
                self.port, 
                self.exit_nodes
            )
            f.write(config_content)
            
        return True

    def start_process(self) -> bool:
        cmd = ['tor', '-f', self.config_file]
        
        try:
            # Используем preexec_fn только на Unix-системах
            if hasattr(os, 'setsid'):
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    preexec_fn=os.setsid
                )
            else:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
            
            self.is_running = True
            return True
        except Exception:
            return False

    def stop_process(self):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
                    
            self.process = None
            self.is_running = False

    def cleanup(self):
        if self.config_file and os.path.exists(self.config_file):
            os.unlink(self.config_file)
            self.config_file = None

    def test_connection(self) -> bool:
        for i, url in enumerate(TEST_URLS):
            try:
                response = self._make_request(url)
                if response:
                    return True
            except Exception as e:
                continue
        return False

    def check_health(self) -> bool:
        for url in TEST_URLS:
            response = self._make_request(url)
            if not response:
                continue

            if 'json' in response.headers.get('content-type', ''):
                try:
                    data = response.json()
                    if 'origin' in data:
                        self.current_exit_ip = data['origin'].strip()
                    elif 'ip' in data:
                        self.current_exit_ip = data['ip'].strip()
                except ValueError:
                    self.current_exit_ip = response.text.strip()
            else:
                self.current_exit_ip = response.text.strip()

            self.failed_checks = 0
            self.last_check = datetime.now()

            if self.current_exit_ip:
                self.report_active_exit_node(self.current_exit_ip)

            return True

        self.failed_checks += 1
        return False

    def get_proxies(self) -> dict:
        return {
            'http': f'socks5://127.0.0.1:{self.port}',
            'https': f'socks5://127.0.0.1:{self.port}'
        }

    def reload_exit_nodes(self, new_exit_nodes: List[str], config_manager) -> bool:
        if not self.process or self.process.poll() is not None:
            return False
        
        try:
            self.exit_nodes = new_exit_nodes
            config_content = config_manager.get_tor_config_by_port(
                self.port,
                self.exit_nodes
            )
            with open(self.config_file, 'w') as f:
                f.write(config_content)
            
            if hasattr(os, 'setsid'):
                os.killpg(os.getpgid(self.process.pid), signal.SIGHUP)
            else:
                self.process.send_signal(signal.SIGHUP)
            
            time.sleep(1)
            return True
        except (OSError, ProcessLookupError, PermissionError, IOError):
            return False

    def report_active_exit_node(self, ip: str):
        self.exit_node_activity[ip] = datetime.now()
        self.node_usage_count[ip] = self.node_usage_count.get(ip, 0) + 1
        
        if ip in self.suspicious_nodes:
            self.suspicious_nodes.discard(ip)

    def get_inactive_exit_nodes(self) -> List[str]:
        current_time = datetime.now()
        inactive = []
        
        for ip, last_seen in self.exit_node_activity.items():
            if current_time - last_seen > self.inactive_threshold:
                inactive.append(ip)
                
        return inactive

    def blacklist_exit_node(self, ip: str):
        self.blacklisted_nodes.add(ip)
        self.suspicious_nodes.discard(ip)
        self.exit_node_activity.pop(ip, None)

    def is_exit_node_healthy(self, ip: str) -> bool:
        return ip not in self.blacklisted_nodes and ip not in self.suspicious_nodes

    def check_inactive_exit_nodes(self):
        current_time = datetime.now()
        newly_suspicious = []
        
        for ip, last_seen in tuple(self.exit_node_activity.items()):
            if current_time - last_seen > self.inactive_threshold:
                if ip not in self.suspicious_nodes and ip not in self.blacklisted_nodes:
                    self.suspicious_nodes.add(ip)
                    newly_suspicious.append(ip)

    def get_exit_node_stats(self) -> dict:
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

    def get_healthy_exit_nodes(self) -> List[str]:
        return [node for node in self.exit_nodes 
               if node not in self.blacklisted_nodes and node not in self.suspicious_nodes]

    def get_suspicious_exit_nodes(self) -> List[str]:
        return list(self.suspicious_nodes)
