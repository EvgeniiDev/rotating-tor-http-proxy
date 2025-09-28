import logging
import threading
from typing import Dict, List

from tor_process import TorInstance
from utils import cleanup_temp_files

class TorParallelRunner:
    """
    Отвечает за параллельный запуск и управление множественными процессами Tor.
    
    Логика:
    - Запускает несколько Tor процессов одновременно через threading
    - Управляет жизненным циклом каждого процесса (старт/стоп/рестарт)
    - Предоставляет thread-safe доступ к статусам всех процессов
    """
    def __init__(self, config_builder, max_workers: int):
        self.config_builder = config_builder
        self.max_workers = max_workers
        self.instances: Dict[int, TorInstance] = {}
        self._lock = threading.RLock()
        self.logger = logging.getLogger(__name__)
        self._shutdown_event = threading.Event()

    def start_many(self, ports: List[int], exit_nodes_list: List[List[str]]) -> List[int]:
        if self._shutdown_event.is_set():
            return []

        started: List[int] = []
        for port, exit_nodes in zip(ports, exit_nodes_list):
            if self._shutdown_event.is_set():
                break
            if self._start_instance(port, exit_nodes):
                started.append(port)
            else:
                with self._lock:
                    self.instances.pop(port, None)
        return started

    def _start_instance(self, port: int, exit_nodes: List[str]):
        instance = TorInstance(port, exit_nodes, self.config_builder)
        instance.create_config()
        started = instance.start()
        healthy = False
        if started:
            if instance.check_health():
                self.logger.info(f"Tor instance on port {port} is healthy")
                healthy = True
            else:
                self.logger.warning(f"Tor instance on port {port} failed health check")
        else:
            self.logger.error(f"Failed to start Tor instance on port {port}")
        with self._lock:
            self.instances[port] = instance if started and healthy else None
        return started and healthy

    def stop_all(self):
        self._shutdown_event.set()
        
        with self._lock:
            for port, instance in self.instances.items():
                if instance:
                    try:
                        instance.stop()
                    except Exception as e:
                        self.logger.debug(f"Error stopping instance on port {port}: {e}")
            self.instances.clear()

    def shutdown(self):
        self._shutdown_event.set()
        self.stop_all()
        cleanup_temp_files()

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass

    def get_statuses(self) -> Dict[int, dict]:
        with self._lock:
            return {
                port: {'port': port, 'is_running': inst.is_running}
                for port, inst in self.instances.items()
                if inst is not None
            }
