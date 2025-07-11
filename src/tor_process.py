import subprocess
import tempfile
import os
import time
import threading
import shutil
from typing import List, Optional, Dict
from datetime import datetime, timedelta
import signal
import requests

class TorInstance:
    """
    Отвечает только за управление одним процессом Tor и мониторинг его здоровья.
    """
    def __init__(self, port: int, exit_nodes: List[str], config_builder):
        self.port = port
        self.exit_nodes = exit_nodes
        self.config_builder = config_builder
        self.process = None
        self.config_file = None
        self.is_running = False
        self.failed_checks = 0
        self.max_failures = 3
        self.last_check = None
        self.current_exit_ip = None
        self._health_thread = None
        self._stop_health = False

    def create_config(self):
        temp_fd, self.config_file = tempfile.mkstemp(suffix='.torrc', prefix=f'tor_{self.port}_')
        with os.fdopen(temp_fd, 'w') as f:
            if self.exit_nodes:
                config_content = self.config_builder.build_config(self.port, self.exit_nodes)
            else:
                config_content = self.config_builder.build_config_without_exit_nodes(self.port)
            f.write(config_content)
        return True

    def start(self):
        cmd = ['tor', '-f', self.config_file]
        try:
            if hasattr(os, 'setsid'):
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, preexec_fn=os.setsid)
            else:
                self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.is_running = True
            self._stop_health = False
            self._health_thread = threading.Thread(target=self._health_monitor, daemon=True)
            self._health_thread.start()
            return True
        except Exception:
            return False

    def stop(self):
        self._stop_health = True
        if self._health_thread:
            self._health_thread.join(timeout=2)
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        self.is_running = False
        
        # Cleanup temporary config file
        if self.config_file and os.path.exists(self.config_file):
            os.unlink(self.config_file)
            self.config_file = None
            
        # Cleanup data directory to avoid disk buildup
        data_dir = os.path.expanduser(f'~/tor-http-proxy/data/data_{self.port}')
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir, ignore_errors=True)

    def _health_monitor(self):
        while not self._stop_health:
            self.check_health()
            time.sleep(5)

    def check_health(self) -> bool:
        url = 'https://api.ipify.org?format=json'
        try:
            response = requests.get(url, proxies=self.get_proxies(), timeout=10)
            if response.status_code == 200:
                self.current_exit_ip = response.json().get('ip')
                self.failed_checks = 0
                self.last_check = datetime.now()
                return True
        except Exception:
            pass
        self.failed_checks += 1
        return False

    def get_proxies(self) -> dict:
        return {'http': f'socks5://127.0.0.1:{self.port}', 'https': f'socks5://127.0.0.1:{self.port}'}

    def get_status(self) -> dict:
        return {
            'port': self.port,
            'is_running': self.is_running,
            'current_exit_ip': self.current_exit_ip,
            'last_check': self.last_check,
            'failed_checks': self.failed_checks
        }
