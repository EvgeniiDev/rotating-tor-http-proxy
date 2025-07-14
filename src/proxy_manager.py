import subprocess
import logging
import time
from typing import List, Dict, Optional

from tor_relay_manager import TorRelayManager
from tor_config_builder import TorConfigBuilder
from polipo_config_builder import PolipoConfigBuilder
from haproxy_config_builder import HAProxyConfigBuilder
from utils import is_port_available

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
            logger.debug(f"Starting Tor process with config: {self.tor_config}")
            self.tor_process = subprocess.Popen([
                'tor', '-f', self.tor_config
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Wait a bit longer for Tor to initialize
            time.sleep(5)
            
            # Check if Tor started successfully
            if self.tor_process.poll() is not None:
                stdout, stderr = self.tor_process.communicate()
                logger.error(f"Tor failed to start on port {self.tor_port}")
                logger.error(f"Tor stderr: {stderr.decode()[:500]}")
                return

            logger.debug(f"Starting Polipo process with config: {self.polipo_config}")
            self.polipo_process = subprocess.Popen([
                'polipo', '-c', self.polipo_config
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            # Wait for Polipo to initialize
            time.sleep(2)
            
            # Check if Polipo started successfully
            if self.polipo_process.poll() is not None:
                stdout, stderr = self.polipo_process.communicate()
                logger.error(f"Polipo failed to start on port {self.http_port}")
                logger.error(f"Polipo stderr: {stderr.decode()[:500]}")
                return

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

    def get_detailed_status(self) -> Dict:
        status = {
            'running': self.running,
            'tor_port': self.tor_port,
            'http_port': self.http_port,
            'tor_healthy': False,
            'polipo_healthy': False,
            'tor_exit_code': None,
            'polipo_exit_code': None
        }
        
        if self.tor_process:
            tor_poll = self.tor_process.poll()
            status['tor_healthy'] = tor_poll is None
            status['tor_exit_code'] = tor_poll
        
        if self.polipo_process:
            polipo_poll = self.polipo_process.poll()
            status['polipo_healthy'] = polipo_poll is None
            status['polipo_exit_code'] = polipo_poll
            
        return status


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
        # Check port availability
        logger.info("Checking port availability...")
        used_ports = []
        for i in range(self.num_proxies):
            tor_port = self.base_tor_port + i
            http_port = self.base_http_port + i
            
            if not is_port_available('127.0.0.1', tor_port):
                used_ports.append(f"Tor port {tor_port}")
            if not is_port_available('127.0.0.1', http_port):
                used_ports.append(f"HTTP port {http_port}")
        
        # Check HAProxy ports
        if not is_port_available('127.0.0.1', 8080):
            used_ports.append("HAProxy main port 8080")
        if not is_port_available('127.0.0.1', 8404):
            used_ports.append("HAProxy stats port 8404")
            
        if used_ports:
            logger.warning(f"Some ports are already in use: {', '.join(used_ports)}")
            logger.warning("This may cause startup failures. Consider stopping other services or changing ports.")
        else:
            logger.info("All required ports are available")
        
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
        haproxy_healthy = self.haproxy_process is not None and self.haproxy_process.poll() is None

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

    def diagnose_issues(self):
        logger.info("=== SYSTEM DIAGNOSIS ===")
        
        # Check individual proxy services
        for i, service in enumerate(self.proxy_services):
            status = service.get_detailed_status()
            logger.info(f"Proxy {i+1} (Tor:{status['tor_port']}, HTTP:{status['http_port']}):")
            logger.info(f"  - Running: {status['running']}")
            logger.info(f"  - Tor healthy: {status['tor_healthy']} (exit_code: {status['tor_exit_code']})")
            logger.info(f"  - Polipo healthy: {status['polipo_healthy']} (exit_code: {status['polipo_exit_code']})")
            
            if not status['tor_healthy'] and status['tor_exit_code'] is not None:
                logger.warning(f"  - Tor process exited with code {status['tor_exit_code']}")
            if not status['polipo_healthy'] and status['polipo_exit_code'] is not None:
                logger.warning(f"  - Polipo process exited with code {status['polipo_exit_code']}")
        
        # Check HAProxy
        haproxy_status = self.haproxy_process is not None and self.haproxy_process.poll() is None
        logger.info(f"HAProxy:")
        logger.info(f"  - Process exists: {self.haproxy_process is not None}")
        if self.haproxy_process:
            exit_code = self.haproxy_process.poll()
            logger.info(f"  - Healthy: {haproxy_status} (exit_code: {exit_code})")
            if exit_code is not None:
                logger.warning(f"  - HAProxy process exited with code {exit_code}")
        
        # Overall status
        healthy_count = sum(1 for service in self.proxy_services if service.is_healthy())
        logger.info(f"Overall: {healthy_count}/{len(self.proxy_services)} proxies healthy, HAProxy: {'OK' if haproxy_status else 'FAILED'}")
        logger.info("=== END DIAGNOSIS ===")

    def restart_failed_services(self):
        logger.info("Attempting to restart failed services...")
        
        for i, service in enumerate(self.proxy_services):
            if not service.is_healthy():
                logger.info(f"Restarting proxy service {i+1}...")
                service.stop()
                time.sleep(2)
                service.start()
                
                if service.is_healthy():
                    logger.info(f"Successfully restarted proxy service {i+1}")
                else:
                    logger.warning(f"Failed to restart proxy service {i+1}")
        
        # Check HAProxy
        if self.haproxy_process and self.haproxy_process.poll() is not None:
            logger.info("Restarting HAProxy...")
            try:
                self.haproxy_process = subprocess.Popen([
                    'haproxy', '-f', self.haproxy_config_builder.config_file
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                logger.info("HAProxy restarted")
            except Exception as e:
                logger.error(f"Failed to restart HAProxy: {e}")
