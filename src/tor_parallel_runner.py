import threading
import os
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
        self._shutdown_event = threading.Event()
        self._executor = None

    def start_many(self, ports: List[int], exit_nodes_list: List[List[str]]):
        if self._shutdown_event.is_set():
            self.logger.warning("Cannot start instances - runner is shutting down")
            return
            
        max_workers = min(self.max_workers, len(ports))
        self.logger.info(f"Starting {len(ports)} Tor processes with max {max_workers} concurrent workers")
        
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="TorStarter")
        
        try:
            future_to_port = {}
            for port, exit_nodes in zip(ports, exit_nodes_list):
                if self._shutdown_event.is_set():
                    break
                future = self._executor.submit(self._start_instance, port, exit_nodes)
                future_to_port[future] = port
            
            completed_count = 0
            successful_count = 0
            
            for future in as_completed(future_to_port):
                if self._shutdown_event.is_set():
                    break
                    
                port = future_to_port[future]
                completed_count += 1
                
                try:
                    result = future.result(timeout=30)
                    if result:
                        successful_count += 1
                        self.logger.info(f"✅ Process {completed_count}/{len(ports)}: Tor on port {port} started successfully")
                    else:
                        self.logger.warning(f"❌ Process {completed_count}/{len(ports)}: Tor on port {port} failed to start")
                except Exception as e:
                    self.logger.error(f"❌ Process {completed_count}/{len(ports)}: Tor on port {port} failed with exception: {e}")
        
        except Exception as e:
            self.logger.error(f"Error during parallel start: {e}")
        
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
        self._shutdown_event.set()
        
        with self._lock:
            for port, instance in self.instances.items():
                if instance:
                    self.logger.info(f"Stopping Tor instance on port {port}")
                    try:
                        instance.stop()
                    except Exception as e:
                        self.logger.error(f"Error stopping instance on port {port}: {e}")
            self.instances.clear()
        self.logger.info("All Tor instances stopped and cleaned up")

    def shutdown(self):
        self.logger.info("Shutting down TorParallelRunner...")
        self._shutdown_event.set()
        
        if self._executor:
            self.logger.info("Shutting down thread pool executor...")
            self._executor.shutdown(wait=True)
            self._executor = None
            
        self.stop_all()
        self._cleanup_temp_files()
        self.logger.info("TorParallelRunner shutdown complete")

    def _cleanup_temp_files(self):
        import glob
        temp_files = glob.glob("/tmp/tor_*")
        if temp_files:
            self.logger.info(f"Cleaning up {len(temp_files)} temporary Tor files...")
            for temp_file in temp_files:
                try:
                    if os.path.isfile(temp_file):
                        os.unlink(temp_file)
                    elif os.path.isdir(temp_file):
                        import shutil
                        shutil.rmtree(temp_file, ignore_errors=True)
                except Exception as e:
                    self.logger.warning(f"Failed to remove {temp_file}: {e}")
            self.logger.info("Temporary Tor files cleaned up")

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass

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
