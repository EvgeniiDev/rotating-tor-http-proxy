import subprocess
import logging
import time
from typing import List, Dict, Optional

from tor_relay_manager import TorRelayManager
from config_manager import TorConfigBuilder
from polipo_config_builder import PolipoConfigBuilder
from haproxy_config_builder import HAProxyConfigBuilder
logger = logging.getLogger(__name__)


class ProxyService:
    def __init__(self, tor_config: str, polipo_config: str, tor_port: int, http_port: int):
        self.tor_config = tor_config
        self.polipo_config = polipo_config
        self.tor_port = tor_port
        self.http_port = http_port
        self.tor_process = None
        self.polipo_process = None
        self.running = False

    def start(self):
        try:
            self.tor_process = subprocess.Popen([
                'tor', '-f', self.tor_config
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            time.sleep(3)

            self.polipo_process = subprocess.Popen([
                'polipo', '-c', self.polipo_config
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            self.running = True
            logger.info(
                f"Started Tor on port {self.tor_port} and Polipo on port {self.http_port}")

        except Exception as e:
            logger.error(f"Failed to start proxy service: {e}")
            self.stop()

    def stop(self):
        self.running = False

        if self.polipo_process:
            try:
                self.polipo_process.terminate()
                self.polipo_process.wait(timeout=5)
            except:
                if self.polipo_process.poll() is None:
                    self.polipo_process.kill()

        if self.tor_process:
            try:
                self.tor_process.terminate()
                self.tor_process.wait(timeout=10)
            except:
                if self.tor_process.poll() is None:
                    self.tor_process.kill()

        logger.info(
            f"Stopped proxy service on ports {self.tor_port}/{self.http_port}")

    def is_healthy(self) -> bool:
        if not self.running:
            return False

        tor_healthy = self.tor_process is not None and self.tor_process.poll() is None
        polipo_healthy = self.polipo_process is not None and self.polipo_process.poll() is None

        return tor_healthy and polipo_healthy


class ProxyManager:
    def __init__(self, num_proxies: int = 10, base_tor_port: int = 9050, base_http_port: int = 8001):
        self.num_proxies = num_proxies
        self.base_tor_port = base_tor_port
        self.base_http_port = base_http_port

        self.relay_manager = TorRelayManager()
        self.tor_config_builder = TorConfigBuilder()
        self.polipo_config_builder = PolipoConfigBuilder()
        self.haproxy_config_builder = HAProxyConfigBuilder()

        self.proxy_services: List[ProxyService] = []
        self.haproxy_process = None
        self.running = False

    def initialize(self):
        logger.info("Fetching Tor relay information...")
        relay_data = self.relay_manager.fetch_tor_relays()
        if not relay_data:
            raise Exception("Failed to fetch Tor relay data")

        exit_nodes = self.relay_manager.extract_relay_ips(relay_data)
        if not exit_nodes:
            raise Exception("No exit nodes found")

        logger.info(f"Found {len(exit_nodes)} exit nodes")

        distributions = self.relay_manager.distribute_exit_nodes(
            self.num_proxies)

        proxy_servers = []

        for i in range(self.num_proxies):
            tor_port = self.base_tor_port + i
            http_port = self.base_http_port + i

            distribution_data = distributions.get(i, {})
            exit_node_ips = distribution_data.get('exit_nodes', []) if isinstance(distribution_data, dict) else []

            tor_config_file = self.tor_config_builder.write_config_file(
                tor_port, exit_node_ips)
            polipo_config_file = self.polipo_config_builder.write_config_file(
                http_port, tor_port)

            proxy_service = ProxyService(
                tor_config=tor_config_file,
                polipo_config=polipo_config_file,
                tor_port=tor_port,
                http_port=http_port
            )

            self.proxy_services.append(proxy_service)
            proxy_servers.append({
                'http_port': http_port,
                'tor_port': tor_port
            })

        haproxy_config_file = self.haproxy_config_builder.write_config_file(
            proxy_servers)
        logger.info(f"Generated HAProxy config at {haproxy_config_file}")

    def start_all_services(self):
        logger.info("Starting all proxy services...")

        for i, service in enumerate(self.proxy_services):
            logger.info(
                f"Starting proxy service {i+1}/{len(self.proxy_services)}")
            service.start()

            if not service.is_healthy():
                logger.warning(f"Proxy service {i+1} failed to start properly")

        logger.info("Starting HAProxy load balancer...")
        try:
            self.haproxy_process = subprocess.Popen([
                'haproxy', '-f', self.haproxy_config_builder.config_file
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            self.running = True
            logger.info("HAProxy started successfully")

        except Exception as e:
            logger.error(f"Failed to start HAProxy: {e}")

    def stop_all_services(self):
        logger.info("Stopping all services...")
        self.running = False

        if self.haproxy_process:
            try:
                self.haproxy_process.terminate()
                self.haproxy_process.wait(timeout=5)
            except:
                if self.haproxy_process.poll() is None:
                    self.haproxy_process.kill()

        for service in self.proxy_services:
            service.stop()

        logger.info("All services stopped")

    def get_status(self) -> Dict:
        healthy_services = sum(
            1 for service in self.proxy_services if service.is_healthy())
        haproxy_healthy = self.haproxy_process and self.haproxy_process.poll() is None

        return {
            'running': self.running,
            'total_proxies': len(self.proxy_services),
            'healthy_proxies': healthy_services,
            'haproxy_healthy': haproxy_healthy,
            'load_balancer_port': 8080
        }

    def cleanup_temp_files(self):
        logger.info("Cleaning up configuration files...")
        self.tor_config_builder.cleanup_config_files()
        self.polipo_config_builder.cleanup_config_files()
        self.haproxy_config_builder.cleanup_config_file()
        logger.info("Configuration files cleaned up")
