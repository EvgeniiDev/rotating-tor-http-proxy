import threading
from typing import List, Dict
from tor_process import TorInstance
import logging

class TorParallelRunner:
    """
    Отвечает за параллельный запуск и управление множественными процессами Tor.
    
    Логика:
    - Запускает несколько Tor процессов одновременно через threading
    - Управляет жизненным циклом каждого процесса (старт/стоп/рестарт)
    - Предоставляет thread-safe доступ к статусам всех процессов
    """
    def __init__(self, config_builder, max_concurrent: int = 20):
        self.config_builder = config_builder
        self.max_concurrent = max_concurrent
        self.instances: Dict[int, TorInstance] = {}
        self._lock = threading.RLock()
        self.logger = logging.getLogger(__name__)

    def start_many(self, ports: List[int], exit_nodes_list: List[List[str]]):
        threads = []
        results = [None] * len(ports)
        def thread_func(idx, port, exit_nodes):
            result = self._start_instance(port, exit_nodes)
            results[idx] = result
        for i, (port, exit_nodes) in enumerate(zip(ports, exit_nodes_list)):
            if i >= self.max_concurrent:
                break
            t = threading.Thread(target=thread_func, args=(i, port, exit_nodes))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        # Оставляем только успешно стартовавшие инстансы
        with self._lock:
            self.instances = {port: inst for port, inst in self.instances.items() if inst is not None and inst.is_running}

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
        with self._lock:
            for instance in self.instances.values():
                instance.stop()
            self.instances.clear()

    def get_statuses(self) -> Dict[int, dict]:
        with self._lock:
            return {port: inst.get_status() for port, inst in self.instances.items()}

    def restart_failed(self):
        with self._lock:
            for port, inst in list(self.instances.items()):
                if inst.failed_checks >= inst.max_failures:
                    inst.stop()
                    inst.create_config()
                    inst.start()
