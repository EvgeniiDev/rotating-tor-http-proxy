import logging
import subprocess
import threading
import time
import requests
import os
import tempfile
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class TorInstanceManager:
    def __init__(self, port: int, exit_nodes: List[str], config_manager, exit_node_monitor=None):
        self.port = port
        self.exit_nodes = exit_nodes
        self.config_manager = config_manager
        self.exit_node_monitor = exit_node_monitor
        
        self.process = None
        self.config_file = None
        self.is_running = False
        self.failed_checks = 0
        self.max_failures = 3
        self.last_check = None
        self.current_exit_ip = None
        self.check_interval = 30
        
        self._lock = threading.RLock()
        self._monitor_thread = None
        self._shutdown_event = threading.Event()
        
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
            
            if self._monitor_thread and self._monitor_thread.is_alive():
                self._monitor_thread.join(timeout=5)
                
            self._stop_tor_process()
            self._cleanup()
            
    def restart(self) -> bool:
        self.stop()
        time.sleep(2)
        return self.start()
        
    def is_healthy(self) -> bool:
        try:
            response = requests.get(
                'http://httpbin.org/ip',
                proxies=self._get_proxies(),
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                self.current_exit_ip = data.get('origin', '').strip()
                self.failed_checks = 0
                self.last_check = datetime.now()
                
                if self.exit_node_monitor and self.current_exit_ip:
                    self.exit_node_monitor.report_active_node(self.current_exit_ip)
                    
                return True
                
        except Exception:
            pass
            
        self.failed_checks += 1
        return False
        
    def get_status(self) -> dict:
        return {
            'port': self.port,
            'is_running': self.is_running,
            'current_exit_ip': self.current_exit_ip,
            'exit_nodes_count': len(self.exit_nodes),
            'failed_checks': self.failed_checks,
            'last_check': self.last_check,
            'process_alive': self.process and self.process.poll() is None
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
                
    def _wait_for_startup(self, timeout: int = 20) -> bool:
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.process and self.process.poll() is not None:
                return False
                
            if self._test_connection():
                return True
                
            time.sleep(1)
            
        return False
        
    def _test_connection(self) -> bool:
        try:
            response = requests.get(
                'http://httpbin.org/ip',
                proxies=self._get_proxies(),
                timeout=10
            )
            return response.status_code == 200
        except:
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
