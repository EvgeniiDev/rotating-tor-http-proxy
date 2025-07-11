import subprocess
import threading
import time
import logging
import signal
import os
from typing import List, Optional, Dict, Set
from datetime import datetime, timedelta
import requests

from tor_config_builder import TorConfigBuilder

logger = logging.getLogger(__name__)

TEST_URLS = [
    'https://httpbin.org/ip',
    'https://api.ipify.org?format=json',
    'https://icanhazip.com'
]

REQUEST_TIMEOUT = 10


class TorProcessManager:
    """
    Класс для управления одним процессом Tor.
    Отвечает за запуск процесса, мониторинг здоровья и сбор статистики об IP.
    Проверяет выходной IP каждые 5 секунд.
    """
    
    def __init__(self, port: int, exit_nodes: List[str], config_builder: TorConfigBuilder):
        self.port = port
        self.exit_nodes = exit_nodes
        self.config_builder = config_builder
        
        # Состояние процесса
        self.process: Optional[subprocess.Popen] = None
        self.config_path: Optional[str] = None
        self.is_running = False
        
        # Мониторинг здоровья
        self.failed_checks = 0
        self.max_failures = 3
        self.last_check: Optional[datetime] = None
        self.current_exit_ip: Optional[str] = None
        
        # Статистика выходных узлов
        self.exit_node_activity: Dict[str, datetime] = {}
        self.suspicious_nodes: Set[str] = set()
        self.blacklisted_nodes: Set[str] = set()
        self.node_usage_count: Dict[str, int] = {}
        self.inactive_threshold = timedelta(minutes=60)
        
        # Мониторинг IP
        self._monitoring_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._monitoring_interval = 5  # проверка каждые 5 секунд
        
    def start(self) -> bool:
        """
        Запускает процесс Tor и начинает мониторинг.
        """
        try:
            # Создаем конфигурацию
            if not self._create_config():
                logger.error(f"Failed to create config for port {self.port}")
                return False
            
            # Запускаем процесс
            if not self._start_process():
                logger.error(f"Failed to start Tor process on port {self.port}")
                self._cleanup_config()
                return False
            
            # Ждем готовности
            if not self._wait_for_startup():
                logger.error(f"Tor process on port {self.port} failed to start properly")
                self.stop()
                return False
            
            self.is_running = True
            self._start_monitoring()
            
            logger.info(f"Tor process started successfully on port {self.port}")
            return True
            
        except Exception as e:
            logger.error(f"Error starting Tor process on port {self.port}: {e}")
            self.stop()
            return False
    
    def stop(self):
        """
        Останавливает процесс Tor и очищает ресурсы.
        """
        self.is_running = False
        self._shutdown_event.set()
        
        # Останавливаем мониторинг
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            self._monitoring_thread.join(timeout=5)
        
        # Останавливаем процесс
        self._stop_process()
        
        # Очищаем ресурсы
        self._cleanup_config()
        self._cleanup_data_directory()
        
    def check_health(self) -> bool:
        """
        Проверяет здоровье процесса Tor.
        """
        if not self.is_running or not self.process:
            return False
        
        # Проверяем, жив ли процесс
        if self.process.poll() is not None:
            logger.warning(f"Tor process on port {self.port} has died")
            self.is_running = False
            return False
        
        # Проверяем соединение
        current_ip = self._get_current_exit_ip()
        if current_ip:
            self.current_exit_ip = current_ip
            self.failed_checks = 0
            self.last_check = datetime.now()
            
            # Обновляем статистику активности узла
            self._report_active_exit_node(current_ip)
            return True
        else:
            self.failed_checks += 1
            logger.warning(f"Failed health check for port {self.port} (failures: {self.failed_checks})")
            return False
    
    def get_status(self) -> Dict:
        """
        Возвращает текущий статус процесса Tor.
        """
        return {
            'port': self.port,
            'is_running': self.is_running,
            'current_exit_ip': self.current_exit_ip,
            'exit_nodes_count': len(self.exit_nodes),
            'failed_checks': self.failed_checks,
            'last_check': self.last_check,
            'process_alive': self.process and self.process.poll() is None,
            'exit_node_stats': self._get_exit_node_stats()
        }
    
    def reload_exit_nodes(self, new_exit_nodes: List[str]) -> bool:
        """
        Перезагружает список выходных узлов.
        """
        if not self.is_running or not self.process or self.process.poll() is not None:
            return False
        
        try:
            self.exit_nodes = new_exit_nodes
            
            # Создаем новую конфигурацию
            new_config_path = self.config_builder.create_temporary_config(self.port, self.exit_nodes)
            
            # Заменяем старую конфигурацию
            if self.config_path:
                self.config_builder.cleanup_config(self.config_path)
            self.config_path = new_config_path
            
            # Отправляем сигнал SIGHUP для перезагрузки конфигурации
            if hasattr(os, 'setsid'):
                os.killpg(os.getpgid(self.process.pid), signal.SIGHUP)
            else:
                self.process.send_signal(signal.SIGHUP)
            
            time.sleep(1)
            logger.info(f"Reloaded exit nodes for port {self.port}")
            return True
            
        except (OSError, ProcessLookupError, PermissionError, IOError) as e:
            logger.error(f"Failed to reload exit nodes for port {self.port}: {e}")
            return False
    
    def _create_config(self) -> bool:
        """
        Создает конфигурационный файл для Tor.
        """
        try:
            self.config_path = self.config_builder.create_temporary_config(self.port, self.exit_nodes)
            return True
        except Exception as e:
            logger.error(f"Failed to create config for port {self.port}: {e}")
            return False
    
    def _start_process(self) -> bool:
        """
        Запускает процесс Tor.
        """
        cmd = ['tor', '-f', self.config_path]
        
        try:
            if hasattr(os, 'setsid'):
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    preexec_fn=os.setsid
                )
            else:
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to start Tor process on port {self.port}: {e}")
            return False
    
    def _wait_for_startup(self, timeout: int = 60) -> bool:
        """
        Ожидает запуска процесса Tor.
        """
        logger.info(f"Waiting for Tor process on port {self.port} to start up...")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.process and self.process.poll() is not None:
                logger.error(f"Tor process on port {self.port} died during startup")
                return False
            
            if self._test_connection():
                logger.info(f"Tor process on port {self.port} is ready")
                return True
            
            time.sleep(2)
        
        logger.warning(f"Tor process on port {self.port} failed to start within {timeout}s")
        return False
    
    def _test_connection(self) -> bool:
        """
        Тестирует соединение через Tor.
        """
        for url in TEST_URLS:
            try:
                response = requests.get(
                    url,
                    proxies=self._get_proxies(),
                    timeout=REQUEST_TIMEOUT
                )
                response.raise_for_status()
                return True
            except requests.RequestException:
                continue
        return False
    
    def _get_current_exit_ip(self) -> Optional[str]:
        """
        Получает текущий выходной IP через Tor.
        """
        for url in TEST_URLS:
            try:
                response = requests.get(
                    url,
                    proxies=self._get_proxies(),
                    timeout=REQUEST_TIMEOUT
                )
                response.raise_for_status()
                
                if 'json' in response.headers.get('content-type', ''):
                    try:
                        data = response.json()
                        if 'origin' in data:
                            return data['origin'].strip()
                        elif 'ip' in data:
                            return data['ip'].strip()
                    except ValueError:
                        return response.text.strip()
                else:
                    return response.text.strip()
                    
            except requests.RequestException:
                continue
        
        return None
    
    def _get_proxies(self) -> Dict[str, str]:
        """
        Возвращает настройки прокси для запросов.
        """
        return {
            'http': f'socks5://127.0.0.1:{self.port}',
            'https': f'socks5://127.0.0.1:{self.port}'
        }
    
    def _start_monitoring(self):
        """
        Запускает поток мониторинга IP каждые 5 секунд.
        """
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            return
        
        self._monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            name=f"TorMonitor-{self.port}"
        )
        self._monitoring_thread.daemon = True
        self._monitoring_thread.start()
    
    def _monitoring_loop(self):
        """
        Основной цикл мониторинга.
        """
        logger.debug(f"Started monitoring for port {self.port}")
        
        while not self._shutdown_event.is_set() and self.is_running:
            try:
                self.check_health()
                self._check_inactive_exit_nodes()
            except Exception as e:
                logger.error(f"Error in monitoring loop for port {self.port}: {e}")
            
            self._shutdown_event.wait(self._monitoring_interval)
        
        logger.debug(f"Stopped monitoring for port {self.port}")
    
    def _stop_process(self):
        """
        Останавливает процесс Tor.
        """
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
            
            self.process = None
    
    def _cleanup_config(self):
        """
        Очищает конфигурационный файл.
        """
        if self.config_path:
            self.config_builder.cleanup_config(self.config_path)
            self.config_path = None
    
    def _cleanup_data_directory(self):
        """
        Очищает директорию данных.
        """
        self.config_builder.cleanup_data_directory(self.port)
    
    def _report_active_exit_node(self, ip: str):
        """
        Регистрирует активность выходного узла.
        """
        self.exit_node_activity[ip] = datetime.now()
        self.node_usage_count[ip] = self.node_usage_count.get(ip, 0) + 1
        
        # Удаляем из подозрительных если узел снова активен
        if ip in self.suspicious_nodes:
            self.suspicious_nodes.discard(ip)
    
    def _check_inactive_exit_nodes(self):
        """
        Проверяет неактивные выходные узлы.
        """
        current_time = datetime.now()
        
        for ip, last_seen in tuple(self.exit_node_activity.items()):
            if current_time - last_seen > self.inactive_threshold:
                if ip not in self.suspicious_nodes and ip not in self.blacklisted_nodes:
                    self.suspicious_nodes.add(ip)
                    logger.warning(f"Exit node {ip} marked as suspicious (inactive for {current_time - last_seen})")
    
    def _get_exit_node_stats(self) -> Dict:
        """
        Возвращает статистику выходных узлов.
        """
        current_time = datetime.now()
        active_count = 0
        inactive_count = 0
        
        for ip, last_seen in self.exit_node_activity.items():
            if current_time - last_seen <= self.inactive_threshold:
                active_count += 1
            else:
                inactive_count += 1
        
        return {
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