import threading
from typing import List, Dict
from tor_process import TorInstance
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

class TorParallelRunner:
    """
    Отвечает за параллельный запуск и управление множественными процессами Tor.
    
    Логика:
    - Запускает несколько Tor процессов одновременно через threading
    - Управляет жизненным циклом каждого процесса (старт/стоп/рестарт)
    - Предоставляет thread-safe доступ к статусам всех процессов
    """
    def __init__(self, config_builder, max_workers: int = 10):
        self.config_builder = config_builder
        self.max_workers = min(max_workers, 10)
        self.instances: Dict[int, TorInstance] = {}
        self._lock = threading.RLock()
        self.logger = logging.getLogger(__name__)

    def start_many(self, ports: List[int], exit_nodes_list: List[List[str]]):
        max_workers = min(self.max_workers, len(ports))
        self.logger.info(f"Starting {len(ports)} Tor processes with max {max_workers} concurrent workers")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Отправляем все задачи в пул
            future_to_port = {}
            for port, exit_nodes in zip(ports, exit_nodes_list):
                future = executor.submit(self._start_instance, port, exit_nodes)
                future_to_port[future] = port
            
            completed_count = 0
            successful_count = 0
            
            # Обрабатываем завершенные задачи по мере их готовности
            for future in as_completed(future_to_port):
                port = future_to_port[future]
                completed_count += 1
                
                try:
                    result = future.result()
                    if result:
                        successful_count += 1
                        self.logger.info(f"✅ Process {completed_count}/{len(ports)}: Tor on port {port} started successfully")
                    else:
                        self.logger.warning(f"❌ Process {completed_count}/{len(ports)}: Tor on port {port} failed to start")
                except Exception as e:
                    self.logger.error(f"❌ Process {completed_count}/{len(ports)}: Tor on port {port} failed with exception: {e}")
        
        # Оставляем только успешно стартовавшие инстансы
        with self._lock:
            self.instances = {port: inst for port, inst in self.instances.items() if inst is not None and inst.is_running}
            
        total_started = len(self.instances)
        self.logger.info(f"All processes completed: {total_started}/{len(ports)} total processes started successfully")

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
        self.logger.info(f"Stopping {len(self.instances)} Tor instances...")
        with self._lock:
            for port, instance in self.instances.items():
                if instance:
                    self.logger.info(f"Stopping Tor instance on port {port}")
                    instance.stop()
            self.instances.clear()
        self.logger.info("All Tor instances stopped and cleaned up")

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
