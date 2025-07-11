import logging
import threading
import time
from typing import List, Dict, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from tor_process_manager import TorProcessManager
from tor_config_builder import TorConfigBuilder

logger = logging.getLogger(__name__)


class TorPoolManager:
    
    def __init__(self, config_builder: TorConfigBuilder, max_concurrent: int = 20):
        self.config_builder = config_builder
        self.max_concurrent = max_concurrent
        
        self.processes: Dict[int, TorProcessManager] = {}
        self.running_processes: Set[int] = set()
        self.failed_processes: Set[int] = set()
        
        self._lock = threading.RLock()
        self._shutdown_event = threading.Event()
        
        self._monitoring_thread: Optional[threading.Thread] = None
        self.is_running = False
        
        self.stats = {
            'total_processes': 0,
            'running_processes': 0,
            'failed_processes': 0,
            'last_update': None
        }
        
    def start_processes(self, process_configs: List[Dict]) -> Dict[str, List[int]]:
        if not process_configs:
            logger.warning("No process configurations provided")
            return {'successful': [], 'failed': []}
        
        configs_to_start = process_configs[:self.max_concurrent]
        if len(process_configs) > self.max_concurrent:
            logger.warning(f"Limiting concurrent starts to {self.max_concurrent} processes "
                         f"(requested {len(process_configs)})")
        
        logger.info(f"Starting {len(configs_to_start)} Tor processes in parallel")
        
        successful_ports = []
        failed_ports = []
        
        with ThreadPoolExecutor(max_workers=min(len(configs_to_start), 10)) as executor:
            future_to_config = {
                executor.submit(self._start_single_process, config): config
                for config in configs_to_start
            }
            
            for future in as_completed(future_to_config):
                config = future_to_config[future]
                port = config['port']
                
                try:
                    success = future.result()
                    if success:
                        successful_ports.append(port)
                        with self._lock:
                            self.running_processes.add(port)
                    else:
                        failed_ports.append(port)
                        with self._lock:
                            self.failed_processes.add(port)
                except Exception as e:
                    logger.error(f"Error starting process on port {port}: {e}")
                    failed_ports.append(port)
                    with self._lock:
                        self.failed_processes.add(port)
        
        if successful_ports:
            self._start_monitoring()
        
        self._update_stats()
        
        logger.info(f"Process startup completed: {len(successful_ports)} successful, "
                   f"{len(failed_ports)} failed")
        
        return {
            'successful': successful_ports,
            'failed': failed_ports
        }
    
    def stop_all_processes(self):
        logger.info("Stopping all Tor processes...")
        
        self.is_running = False
        self._shutdown_event.set()
        
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            self._monitoring_thread.join(timeout=10)
        
        processes_to_stop = []
        with self._lock:
            processes_to_stop = list(self.processes.values())
        
        if processes_to_stop:
            with ThreadPoolExecutor(max_workers=min(len(processes_to_stop), 20)) as executor:
                futures = [executor.submit(process.stop) for process in processes_to_stop]
                
                for future in as_completed(futures):
                    try:
                        future.result(timeout=5)
                    except Exception as e:
                        logger.error(f"Error stopping process: {e}")
        
        with self._lock:
            self.processes.clear()
            self.running_processes.clear()
            self.failed_processes.clear()
        
        self._update_stats()
        logger.info("All Tor processes stopped")
    
    def stop_process(self, port: int) -> bool:
        with self._lock:
            if port not in self.processes:
                logger.warning(f"Process on port {port} not found")
                return False
            
            process = self.processes[port]
        
        try:
            process.stop()
            
            with self._lock:
                del self.processes[port]
                self.running_processes.discard(port)
                self.failed_processes.discard(port)
            
            self._update_stats()
            logger.info(f"Process on port {port} stopped successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error stopping process on port {port}: {e}")
            return False
    
    def get_process_status(self, port: int) -> Optional[Dict]:
        with self._lock:
            if port not in self.processes:
                return None
            return self.processes[port].get_status()
    
    def get_all_statuses(self) -> List[Dict]:
        statuses = []
        with self._lock:
            for process in self.processes.values():
                statuses.append(process.get_status())
        return statuses
    
    def check_process_health(self, port: int) -> bool:
        with self._lock:
            if port not in self.processes:
                return False
            return self.processes[port].check_health()
    
    def restart_failed_processes(self) -> Dict[str, List[int]]:
        failed_ports = []
        with self._lock:
            failed_ports = list(self.failed_processes)
        
        if not failed_ports:
            logger.info("No failed processes to restart")
            return {'successful': [], 'failed': []}
        
        logger.info(f"Restarting {len(failed_ports)} failed processes")
        
        restart_configs = []
        for port in failed_ports:
            with self._lock:
                if port in self.processes:
                    process = self.processes[port]
                    restart_configs.append({
                        'port': port,
                        'exit_nodes': process.exit_nodes
                    })
        
        with self._lock:
            for port in failed_ports:
                if port in self.processes:
                    self.processes[port].stop()
                    del self.processes[port]
            self.failed_processes.clear()
        
        return self.start_processes(restart_configs)
    
    def get_stats(self) -> Dict:
        with self._lock:
            return self.stats.copy()
    
    def _start_single_process(self, config: Dict) -> bool:
        port = config['port']
        exit_nodes = config.get('exit_nodes', [])
        
        try:
            logger.info(f"Starting Tor process on port {port} with {len(exit_nodes)} exit nodes")
            
            process = TorProcessManager(port, exit_nodes, self.config_builder)
            
            with self._lock:
                self.processes[port] = process
            
            success = process.start()
            
            if success:
                logger.info(f"Tor process on port {port} started successfully")
            else:
                logger.error(f"Failed to start Tor process on port {port}")
                with self._lock:
                    if port in self.processes:
                        del self.processes[port]
            
            return success
            
        except Exception as e:
            logger.error(f"Error starting Tor process on port {port}: {e}")
            with self._lock:
                if port in self.processes:
                    del self.processes[port]
            return False
    
    def _start_monitoring(self):
        if self._monitoring_thread and self._monitoring_thread.is_alive():
            return
        
        self.is_running = True
        self._monitoring_thread = threading.Thread(
            target=self._monitoring_loop,
            name="TorPoolMonitor"
        )
        self._monitoring_thread.daemon = True
        self._monitoring_thread.start()
    
    def _monitoring_loop(self):
        logger.debug("Started pool monitoring")
        
        while not self._shutdown_event.is_set() and self.is_running:
            try:
                self._check_process_health()
                self._update_stats()
            except Exception as e:
                logger.error(f"Error in pool monitoring loop: {e}")
            
            self._shutdown_event.wait(30)
        
        logger.debug("Stopped pool monitoring")
    
    def _check_process_health(self):
        dead_ports = []
        
        with self._lock:
            for port, process in self.processes.items():
                if not process.is_running or not process.check_health():
                    if process.failed_checks >= process.max_failures:
                        dead_ports.append(port)
                        logger.warning(f"Process on port {port} marked as dead "
                                     f"(failed checks: {process.failed_checks})")
        
        for port in dead_ports:
            with self._lock:
                self.running_processes.discard(port)
                self.failed_processes.add(port)
    
    def _update_stats(self):
        with self._lock:
            self.stats.update({
                'total_processes': len(self.processes),
                'running_processes': len(self.running_processes),
                'failed_processes': len(self.failed_processes),
                'last_update': datetime.now()
            })