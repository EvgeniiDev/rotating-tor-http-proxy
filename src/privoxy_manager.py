import os
import logging
import subprocess
import time
from typing import Dict, Optional
from config_manager import ConfigManager

logger = logging.getLogger(__name__)


class PrivoxyManager:
    def __init__(self):
        self.config_manager = ConfigManager()
        self.base_socks_port = 10000  # SOCKS порты для Tor (10000-19999)
        self.privoxy_process = None
        self.config_path = "/privoxy.conf"  # Статический конфиг
        
    def start_privoxy(self) -> bool:
        """Запуск Privoxy со статическим конфигом"""
        try:
            if self.is_running():
                logger.info("Privoxy already running")
                return True
                
            # Проверяем наличие конфига
            if not os.path.exists(self.config_path):
                logger.error(f"Privoxy config not found: {self.config_path}")
                return False
                
            # Запускаем Privoxy
            cmd = ['privoxy', '--no-daemon', self.config_path]
            
            self.privoxy_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Проверяем, что процесс запустился
            time.sleep(3)
            if self.privoxy_process.poll() is None:
                logger.info("Privoxy started with static config")
                return True
            else:
                logger.error("Failed to start Privoxy")
                return False
                
        except Exception as e:
            logger.error(f"Error starting Privoxy: {e}")
            return False

    def stop_privoxy(self) -> bool:
        """Остановка Privoxy"""
        try:
            if self.privoxy_process and self.privoxy_process.poll() is None:
                self.privoxy_process.terminate()
                time.sleep(2)
                if self.privoxy_process.poll() is None:
                    self.privoxy_process.kill()
                
                self.privoxy_process = None
                logger.info("Privoxy stopped")
                return True
            return False
            
        except Exception as e:
            logger.error(f"Error stopping Privoxy: {e}")
            return False

    def is_running(self) -> bool:
        """Проверка, запущен ли Privoxy"""
        return bool(self.privoxy_process and self.privoxy_process.poll() is None)

    def get_http_port(self, instance_id: int) -> Optional[int]:
        """Получение HTTP порта Privoxy для экземпляра (SOCKS + 10000)"""
        if 1 <= instance_id <= 100:
            return self.base_socks_port + instance_id - 1 + 10000
        return None

    def get_socks_port(self, instance_id: int) -> Optional[int]:
        """Получение SOCKS порта Tor для экземпляра"""
        if 1 <= instance_id <= 100:
            return self.base_socks_port + instance_id - 1
        return None

