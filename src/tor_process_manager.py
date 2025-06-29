import logging
import subprocess
import threading
from typing import List

logger = logging.getLogger(__name__)


class TorProcessManager:
    def __init__(self, config_manager, load_balancer):
        self.config_manager = config_manager
        self.load_balancer = load_balancer
        self.port_processes = {}
        self.port_exit_nodes = {}
        self._lock = threading.RLock()

    def _start_instance(self, exit_nodes: List[str]):
        port = self._get_available_port()

        try:
            tor_config_result = self.config_manager.create_tor_config_by_port(
                port, exit_nodes)
            tor_cmd = ['tor', '-f', tor_config_result['config_path']]

            logger.info(
                f"Starting Tor instance on port {port} with {len(exit_nodes)} exit nodes")

            process = subprocess.Popen(
                tor_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            import time
            time.sleep(5)

            if process.poll() is not None:
                stdout, stderr = process.communicate()
                logger.error(
                    f"Tor process failed to start on port {port}. Exit code: {process.returncode}")
                if stderr:
                    logger.error(f"Tor stderr: {stderr[:500]}")
                return None, None

            with self._lock:
                self.port_processes[port] = process
                self.port_exit_nodes[port] = exit_nodes
                self.load_balancer.add_proxy(port)

            logger.info(f"Started Tor instance on socks5 port {port}")
            return process, port

        except Exception as e:
            logger.error(
                f"Exception starting Tor instance on port {port}: {e}")
            return None, None

    def _get_available_port(self):
        start_port = 10000
        with self._lock:
            while start_port in self.port_processes:
                start_port += 1
            return start_port

    def _stop_instance(self, port):
        with self._lock:
            if port in self.port_processes:
                self.load_balancer.remove_proxy(port)
                del self.port_processes[port]
                if port in self.port_exit_nodes:
                    del self.port_exit_nodes[port]
                logger.info(
                    f"Removed SOCKS5 proxy port {port} from HTTP load balancer")

    def start_tor_instance(self, exit_nodes: List[str]):
        process, port = self._start_instance(exit_nodes)
        if port is None:
            logger.error(f"Failed to start Tor instance with {len(exit_nodes)} exit nodes")
            return None
        return port

    def stop_tor_instance(self, port):
        with self._lock:
            if port in self.port_processes:
                process = self.port_processes[port]
                if process and process.poll() is None:
                    process.terminate()
                self._stop_instance(port)

    def restart_instance_by_port(self, port, exit_nodes: List[str]):
        logger.info(
            f"Restarting Tor instance on port {port} with {len(exit_nodes)} exit nodes")

        with self._lock:
            old_process = self.port_processes.get(port)

            if old_process and old_process.poll() is None:
                old_process.terminate()
            if old_process and old_process.poll() is None:
                old_process.terminate()
                try:
                    old_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    old_process.kill()

            self._stop_instance(port)

            new_process, new_port = self._start_instance(exit_nodes)

            if new_process and new_port:
                logger.info(
                    f"Successfully restarted Tor instance on port {new_port} with {len(exit_nodes)} exit nodes")
                return new_port
            else:
                logger.error(
                    f"Failed to restart Tor instance with {len(exit_nodes)} exit nodes")
                return False

    def _count_processes(self, ports):
        return sum(1 for port in ports if port in self.port_processes and
                   self.port_processes[port] and self.port_processes[port].poll() is None)

    def count_running_instances(self):
        with self._lock:
            return len([p for p in self.port_processes.values() 
                       if p and p.poll() is None])

    def get_failed_instances(self):
        failed_ports = []

        with self._lock:
            for port, process in self.port_processes.items():
                if not (process and process.poll() is None):
                    exit_nodes_count = len(self.port_exit_nodes.get(port, []))
                    failed_ports.append(f"tor-{port}-{exit_nodes_count}nodes")

        return failed_ports

    def stop_all_instances(self):
        with self._lock:
            for port, process in list(self.port_processes.items()):
                if process and process.poll() is None:
                    process.terminate()
                self._stop_instance(port)

        logger.info("All Tor processes stopped")

    def get_all_ports(self):
        with self._lock:
            return list(self.port_processes.keys())

    def get_port_exit_nodes(self, port):
        with self._lock:
            return self.port_exit_nodes.get(port, [])
