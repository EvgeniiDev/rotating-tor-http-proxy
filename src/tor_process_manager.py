import logging
import subprocess
import threading

logger = logging.getLogger(__name__)


class TorProcessManager:
    def __init__(self, config_manager, load_balancer):
        self.config_manager = config_manager
        self.load_balancer = load_balancer
        self.port_processes = {}
        self.subnet_ports = {}
        self.port_subnets = {}
        self._lock = threading.RLock()

    def _get_subnet_key(self, subnet):
        return f"subnet_{subnet.replace('.', '_')}"

    def _start_instance(self, subnet):
        port = self._get_available_port()

        try:
            tor_config_result = self.config_manager.create_tor_config_by_port(
                port, subnet)
            tor_cmd = ['tor', '-f', tor_config_result['config_path']]

            logger.info(
                f"Starting Tor instance on port {port} for subnet {subnet}")

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
                    f"Tor process failed to start on port {port} for subnet {subnet}. Exit code: {process.returncode}")
                if stderr:
                    logger.error(f"Tor stderr: {stderr[:500]}")
                return None, None

            with self._lock:
                self.port_processes[port] = process
                self.port_subnets[port] = subnet
                self.load_balancer.add_proxy(port)

            logger.info(f"Started Tor instance on socks5 port {port}")
            return process, port

        except Exception as e:
            logger.error(
                f"Exception starting Tor instance on port {port} for subnet {subnet}: {e}")
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
                if port in self.port_subnets:
                    del self.port_subnets[port]
                logger.info(
                    f"Removed SOCKS5 proxy port {port} from HTTP load balancer")

    def start_tor_instance(self, subnet):
        process, port = self._start_instance(subnet)
        if port is None:
            logger.error(f"Failed to start Tor instance for subnet {subnet}")
            return None
        return port

    def stop_tor_instance(self, port):
        with self._lock:
            if port in self.port_processes:
                process = self.port_processes[port]
                if process and process.poll() is None:
                    process.terminate()
                self._stop_instance(port)

    def start_subnet_instances(self, subnet, instances_count=1):
        subnet_key = self._get_subnet_key(subnet)
        started_ports = []

        with self._lock:
            if subnet_key not in self.subnet_ports:
                self.subnet_ports[subnet_key] = set()

            for i in range(instances_count):
                process, port = self._start_instance(subnet)

                if process and port:
                    self.subnet_ports[subnet_key].add(port)
                    started_ports.append(port)
                else:
                    logger.error(
                        f"Failed to start Tor instance {i+1}/{instances_count} for subnet {subnet}")

                    for cleanup_port in started_ports:
                        self.stop_tor_instance(cleanup_port)
                    return False, []

        logger.info(
            f"Started {len(started_ports)} Tor instances for subnet {subnet}")
        return True, started_ports

    def stop_subnet_instances(self, subnet):
        subnet_key = self._get_subnet_key(subnet)

        with self._lock:
            if subnet_key in self.subnet_ports:
                ports_to_stop = list(self.subnet_ports[subnet_key])

                for port in ports_to_stop:
                    process = self.port_processes.get(port)
                    if process:
                        self._terminate_process(process, port, subnet)
                    self._stop_instance(port)

                del self.subnet_ports[subnet_key]

        logger.info(f"Stopped all instances for subnet {subnet}")
        return True

    def _terminate_process(self, process, port, subnet):
        if process and process.poll() is None:
            process.terminate()
            logger.info(
                f"Stopped Tor instance on port {port} for subnet {subnet}")

    def restart_instance_by_port(self, port, subnet):
        logger.info(
            f"Restarting Tor instance on port {port} for subnet {subnet}")

        with self._lock:
            old_process = self.port_processes.get(port)

            if old_process and old_process.poll() is None:
                old_process.terminate()
                try:
                    old_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    old_process.kill()

            self._stop_instance(port)

            new_process, new_port = self._start_instance(subnet)

            if new_process and new_port:
                subnet_key = self._get_subnet_key(subnet)
                if subnet_key in self.subnet_ports:
                    self.subnet_ports[subnet_key].discard(port)
                    self.subnet_ports[subnet_key].add(new_port)

                logger.info(
                    f"Successfully restarted Tor instance on port {new_port} for subnet {subnet}")
                return new_port
            else:
                logger.error(
                    f"Failed to restart Tor instance for subnet {subnet}")
                return False

    def _count_processes(self, ports):
        return sum(1 for port in ports if port in self.port_processes and
                   self.port_processes[port] and self.port_processes[port].poll() is None)

    def get_subnet_running_instances(self, subnet):
        subnet_key = self._get_subnet_key(subnet)

        with self._lock:
            subnet_ports = self.subnet_ports.get(subnet_key, set())
            return self._count_processes(subnet_ports)

    def count_running_instances(self):
        with self._lock:
            running_main = 0

            running_subnet = sum(
                self._count_processes(ports)
                for ports in self.subnet_ports.values()
            )

            return running_main, running_subnet

    def get_failed_instances(self):
        failed_ports = []

        with self._lock:
            for port, process in self.port_processes.items():
                if not (process and process.poll() is None):
                    subnet = self.port_subnets.get(port, 'unknown')
                    failed_ports.append(f"tor-{port}-{subnet}")

        return failed_ports

    def stop_all_instances(self):
        with self._lock:
            for port, process in list(self.port_processes.items()):
                if process and process.poll() is None:
                    process.terminate()
                self._stop_instance(port)

            self.subnet_ports.clear()

        logger.info("All Tor processes stopped")

    def get_all_ports(self):
        with self._lock:
            return list(self.port_processes.keys())
