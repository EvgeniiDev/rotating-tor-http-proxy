import threading
from typing import List, Dict
from tor_process import TorInstance

class TorParallelRunner:
    """
    Отвечает за параллельный запуск и мониторинг до 20 процессов Tor.
    """
    def __init__(self, config_builder, max_concurrent: int = 20):
        self.config_builder = config_builder
        self.max_concurrent = max_concurrent
        self.instances: Dict[int, TorInstance] = {}
        self._lock = threading.RLock()

    def start_many(self, ports: List[int], exit_nodes_list: List[List[str]]):
        threads = []
        for i, (port, exit_nodes) in enumerate(zip(ports, exit_nodes_list)):
            if i >= self.max_concurrent:
                break
            t = threading.Thread(target=self._start_instance, args=(port, exit_nodes))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    def _start_instance(self, port: int, exit_nodes: List[str]):
        instance = TorInstance(port, exit_nodes, self.config_builder)
        instance.create_config()
        instance.start()
        with self._lock:
            self.instances[port] = instance

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
