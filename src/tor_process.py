import subprocess
import tempfile
import os
import time
import threading
import shutil
from typing import List
import signal
import requests
import logging

class TorInstance:
    """
    Отвечает за управление одним процессом Tor и мониторинг его состояния.
    
    Логика:
    - Создает конфигурацию и запускает/останавливает процесс Tor
    - Проверяет здоровье процесса через SOCKS соединение
    - Поддерживает горячую перезагрузку exit-нод через SIGHUP без перезапуска
    """
    def __init__(self, port: int, exit_nodes: List[str], config_builder):
        self.port = port
        self.exit_nodes = exit_nodes
        self.config_builder = config_builder
        self.process = None
        self.config_file = None
        self.is_running = False
        self.logger = logging.getLogger(__name__)

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
                self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, preexec_fn=os.setsid, text=True)
            else:
                self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            self.is_running = True
            self.logger.info(f"Tor process started on port {self.port}, PID: {self.process.pid}")
            
            time.sleep(2)
            
            poll_result = self.process.poll()
            if poll_result is not None:
                stdout, stderr = self.process.communicate(timeout=5)
                self.logger.error(f"Tor process on port {self.port} exited with code {poll_result}")
                self.logger.error(f"STDOUT: {stdout}")
                self.logger.error(f"STDERR: {stderr}")
                self.is_running = False
                return False
            
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to start Tor process on port {self.port}: {e}", exc_info=True)
            return False

    def stop(self):
        self.logger.info(f"Stopping Tor instance on port {self.port}")
        
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
                self.logger.info(f"Tor process on port {self.port} terminated gracefully")
            except subprocess.TimeoutExpired:
                self.logger.warning(f"Tor process on port {self.port} did not terminate, killing...")
                self.process.kill()
                self.process.wait()
                self.logger.info(f"Tor process on port {self.port} killed")
        
        self.process = None
        self.is_running = False
        
        if self.config_file and os.path.exists(self.config_file):
            os.unlink(self.config_file)
            self.config_file = None
            
        data_dir = os.path.expanduser(f'~/tor-http-proxy/data/data_{self.port}')
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir, ignore_errors=True)

    def check_health(self) -> bool:
        if not self.process:
            self.logger.debug(f"Port {self.port}: No process to check")
            return False
            
        poll_result = self.process.poll()
        if poll_result is not None:
            self.logger.warning(f"Port {self.port}: Process died with exit code {poll_result}")
            self.is_running = False
            return False
        
        url = 'https://api.ipify.org?format=json'
        try:
            response = requests.get(url, proxies=self.get_proxies(), timeout=60)
            if response.status_code == 200:
                return True
        except Exception as e:
            self.logger.debug(f"Port {self.port}: Health check failed: {e}")
            pass
        return False

    def get_proxies(self) -> dict:
        return {'http': f'socks5://127.0.0.1:{self.port}', 'https': f'socks5://127.0.0.1:{self.port}'}

    def reconfigure(self, new_exit_nodes: List[str]) -> bool:
        """
        Reconfigures the Tor instance with new exit nodes using SIGHUP signal.
        """
        try:
            self.logger.info(f"Reconfiguring port {self.port} with nodes: {new_exit_nodes}")
            
            if not self.process:
                self.logger.error(f"Port {self.port}: No process found")
                return False
                
            poll_result = self.process.poll()
            if poll_result is not None:
                self.logger.error(f"Port {self.port}: Process is not running (poll={poll_result})")
                return False
                
            self.logger.info(f"Port {self.port}: Process is running, updating config")
            
            self.exit_nodes = new_exit_nodes
            
            with open(self.config_file, 'w') as f:
                config_content = self.config_builder.build_config(self.port, new_exit_nodes)
                f.write(config_content)
            
            self.logger.info(f"Port {self.port}: Config written, sending SIGHUP")
            self.process.send_signal(signal.SIGHUP)
            self.logger.info(f"Port {self.port}: SIGHUP sent successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Reconfiguration failed for port {self.port}: {e}", exc_info=True)
            return False

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass
