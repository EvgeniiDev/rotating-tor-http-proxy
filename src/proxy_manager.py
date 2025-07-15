import subprocess
import logging
import time
import os
from typing import List, Dict

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
        self.tor_restart_attempts = 0
        self.max_tor_restarts = 3

    def start_tor_with_retry(self):
        """Start Tor with retry mechanism"""
        for attempt in range(self.max_tor_restarts + 1):
            try:
                if attempt > 0:
                    logger.warning(
                        f"Tor restart attempt {attempt}/{self.max_tor_restarts} for port {self.tor_port}")
                    time.sleep(2 * attempt)

                if not is_port_available('127.0.0.1', self.tor_port):
                    logger.error(
                        f"Port {self.tor_port} is already in use, cannot start Tor")
                    if attempt < self.max_tor_restarts:
                        from utils import kill_process_on_port
                        if kill_process_on_port(self.tor_port):
                            time.sleep(3)
                            continue
                    return False

                logger.debug(
                    f"Starting Tor process with config: {self.tor_config}")
                self.tor_process = subprocess.Popen([
                    'tor', '-f', self.tor_config
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

                max_wait_time = 20
                check_interval = 1

                for wait_time in range(0, max_wait_time, check_interval):
                    time.sleep(check_interval)

                    if self.tor_process.poll() is not None:
                        stdout, stderr = self.tor_process.communicate()
                        logger.error(
                            f"Tor failed to start on port {self.tor_port} (attempt {attempt + 1})")
                        logger.error(
                            f"Tor stdout: {stdout[:1000] if stdout else 'No stdout'}")
                        logger.error(
                            f"Tor stderr: {stderr[:1000] if stderr else 'No stderr'}")
                        self.tor_process = None
                        break

                    if not is_port_available('127.0.0.1', self.tor_port):
                        logger.info(
                            f"Tor successfully bound to port {self.tor_port} (attempt {attempt + 1})")
                        self.tor_restart_attempts = attempt
                        return True
                else:
                    logger.warning(
                        f"Tor on port {self.tor_port} may not be fully ready after {max_wait_time}s (attempt {attempt + 1})")
                    if self.tor_process and self.tor_process.poll() is None:
                        return True

            except Exception as e:
                logger.error(
                    f"Exception starting Tor on port {self.tor_port} (attempt {attempt + 1}): {e}")
                if self.tor_process:
                    try:
                        self.tor_process.terminate()
                        self.tor_process.wait(timeout=5)
                    except:
                        if self.tor_process.poll() is None:
                            self.tor_process.kill()
                    self.tor_process = None

        logger.error(
            f"Failed to start Tor on port {self.tor_port} after {self.max_tor_restarts + 1} attempts")
        return False

    def start(self):
        try:
            if not self.start_tor_with_retry():
                logger.error(
                    f"Failed to start Tor on port {self.tor_port} after all retry attempts")
                return

            if not is_port_available('127.0.0.1', self.http_port):
                logger.error(
                    f"Port {self.http_port} is already in use, cannot start Polipo")
                from utils import kill_process_on_port
                if not kill_process_on_port(self.http_port):
                    return
                time.sleep(2)

            logger.debug(
                f"Starting Polipo process with config: {self.polipo_config}")
            self.polipo_process = subprocess.Popen([
                'polipo', '-c', self.polipo_config
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

            time.sleep(3)

            if self.polipo_process.poll() is not None:
                stdout, stderr = self.polipo_process.communicate()
                logger.error(
                    f"Polipo failed to start on port {self.http_port}")
                logger.error(
                    f"Polipo stdout: {stdout[:1000] if stdout else 'No stdout'}")
                logger.error(
                    f"Polipo stderr: {stderr[:1000] if stderr else 'No stderr'}")
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
            'polipo_exit_code': None,
            'tor_restart_attempts': self.tor_restart_attempts,
            'tor_stdout': '',
            'tor_stderr': '',
            'polipo_stdout': '',
            'polipo_stderr': ''
        }

        if self.tor_process:
            tor_poll = self.tor_process.poll()
            status['tor_healthy'] = tor_poll is None
            status['tor_exit_code'] = tor_poll

            if tor_poll is not None and self.tor_process.stdout and self.tor_process.stderr:
                try:
                    stdout, stderr = self.tor_process.communicate(timeout=1)
                    status['tor_stdout'] = stdout[:500] if stdout else ''
                    status['tor_stderr'] = stderr[:500] if stderr else ''
                except:
                    pass

        if self.polipo_process:
            polipo_poll = self.polipo_process.poll()
            status['polipo_healthy'] = polipo_poll is None
            status['polipo_exit_code'] = polipo_poll

            if polipo_poll is not None and self.polipo_process.stdout and self.polipo_process.stderr:
                try:
                    stdout, stderr = self.polipo_process.communicate(timeout=1)
                    status['polipo_stdout'] = stdout[:500] if stdout else ''
                    status['polipo_stderr'] = stderr[:500] if stderr else ''
                except:
                    pass

        return status

    def restart_if_failed(self):
        """Restart service if it has failed"""
        if not self.is_healthy():
            logger.info(
                f"Restarting failed proxy service on ports {self.tor_port}/{self.http_port}")
            self.stop()
            time.sleep(3)
            self.start()
            return self.is_healthy()
        return True


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
        # Check port availability and find alternatives if needed
        logger.info("Checking port availability...")
        adjusted_ports = []

        for i in range(self.num_proxies):
            tor_port = self.base_tor_port + i
            http_port = self.base_http_port + i

            # Find available Tor port
            if not is_port_available('127.0.0.1', tor_port):
                try:
                    from utils import find_available_port
                    tor_port = find_available_port(
                        '127.0.0.1', tor_port + 1000)
                    logger.warning(
                        f"Tor port {self.base_tor_port + i} in use, using {tor_port} instead")
                except RuntimeError:
                    raise Exception(
                        f"Cannot find available port for Tor instance {i}")

            # Find available HTTP port
            if not is_port_available('127.0.0.1', http_port):
                try:
                    from utils import find_available_port
                    http_port = find_available_port(
                        '127.0.0.1', http_port + 1000)
                    logger.warning(
                        f"HTTP port {self.base_http_port + i} in use, using {http_port} instead")
                except RuntimeError:
                    raise Exception(
                        f"Cannot find available port for HTTP instance {i}")

            adjusted_ports.append((tor_port, http_port))

        # Check HAProxy ports
        haproxy_main_port = 8080
        haproxy_stats_port = 8404

        if not is_port_available('127.0.0.1', haproxy_main_port):
            logger.warning(f"HAProxy main port {haproxy_main_port} is in use")
        if not is_port_available('127.0.0.1', haproxy_stats_port):
            logger.warning(
                f"HAProxy stats port {haproxy_stats_port} is in use")

        logger.info("All required ports checked and adjusted if necessary")

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
            tor_port, http_port = adjusted_ports[i]

            distribution_data = distributions.get(i, {})
            exit_node_ips = distribution_data.get(
                'exit_nodes', []) if isinstance(distribution_data, dict) else []

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
        failed_services = []

        for i, service in enumerate(self.proxy_services):
            logger.info(
                f"Starting proxy service {i+1}/{len(self.proxy_services)}")
            service.start()

            if not service.is_healthy():
                logger.warning(f"Proxy service {i+1} failed to start properly")
                failed_services.append(i)

                # Add small delay before next attempt to avoid resource conflicts
                time.sleep(2)

        # Retry failed services with different strategy
        if failed_services:
            logger.info(f"Retrying {len(failed_services)} failed services...")
            for i in failed_services:
                service = self.proxy_services[i]
                logger.info(f"Retrying proxy service {i+1}...")

                # Stop and cleanup first
                service.stop()
                time.sleep(3)

                # Try starting again
                service.start()

                if service.is_healthy():
                    logger.info(f"Successfully restarted proxy service {i+1}")
                    failed_services.remove(i)
                else:
                    logger.error(f"Proxy service {i+1} failed again on retry")

        healthy_count = sum(
            1 for service in self.proxy_services if service.is_healthy())
        logger.info(
            f"Started {healthy_count}/{len(self.proxy_services)} proxy services successfully")

        logger.info("Starting HAProxy load balancer...")
        try:
            from utils import setup_haproxy_logging
            setup_haproxy_logging()

            self.haproxy_process = subprocess.Popen([
                'haproxy', '-f', self.haproxy_config_builder.config_file
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

            time.sleep(3)
            if self.haproxy_process.poll() is not None:
                stdout, stderr = self.haproxy_process.communicate()
                logger.error(f"HAProxy failed to start")
                logger.error(
                    f"HAProxy stdout: {stdout[:1000] if stdout else 'No stdout'}")
                logger.error(
                    f"HAProxy stderr: {stderr[:1000] if stderr else 'No stderr'}")
                logger.error(
                    f"HAProxy config file: {self.haproxy_config_builder.config_file}")

                try:
                    with open(self.haproxy_config_builder.config_file, 'r') as f:
                        config_content = f.read()
                        logger.error(
                            f"HAProxy config content:\n{config_content}")
                except Exception as e:
                    logger.error(f"Could not read HAProxy config: {e}")

                from utils import check_haproxy_logs
                haproxy_logs = check_haproxy_logs()
                if haproxy_logs:
                    logger.error(f"Recent HAProxy logs:\n{haproxy_logs}")

                return

            self.running = True
            logger.info("HAProxy started successfully")
            logger.info(
                "HAProxy logs available at /var/log/haproxy.log or via 'journalctl -u haproxy'")

        except Exception as e:
            logger.error(f"Failed to start HAProxy: {e}")
            import traceback
            logger.error(
                f"HAProxy startup traceback: {traceback.format_exc()}")

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

        for i, service in enumerate(self.proxy_services):
            status = service.get_detailed_status()
            logger.info(
                f"Proxy {i+1} (Tor:{status['tor_port']}, HTTP:{status['http_port']}):")
            logger.info(f"  - Running: {status['running']}")
            logger.info(
                f"  - Tor healthy: {status['tor_healthy']} (exit_code: {status['tor_exit_code']})")
            logger.info(
                f"  - Polipo healthy: {status['polipo_healthy']} (exit_code: {status['polipo_exit_code']})")
            logger.info(
                f"  - Tor restart attempts: {status['tor_restart_attempts']}")

            if not status['tor_healthy'] and status['tor_exit_code'] is not None:
                logger.warning(
                    f"  - Tor process exited with code {status['tor_exit_code']}")
                if status['tor_stderr']:
                    logger.error(f"  - Tor stderr: {status['tor_stderr']}")
                if status['tor_stdout']:
                    logger.error(f"  - Tor stdout: {status['tor_stdout']}")

            if not status['polipo_healthy'] and status['polipo_exit_code'] is not None:
                logger.warning(
                    f"  - Polipo process exited with code {status['polipo_exit_code']}")
                if status['polipo_stderr']:
                    logger.error(
                        f"  - Polipo stderr: {status['polipo_stderr']}")
                if status['polipo_stdout']:
                    logger.error(
                        f"  - Polipo stdout: {status['polipo_stdout']}")

        haproxy_status = self.haproxy_process is not None and self.haproxy_process.poll() is None
        logger.info(f"HAProxy:")
        logger.info(f"  - Process exists: {self.haproxy_process is not None}")
        if self.haproxy_process:
            exit_code = self.haproxy_process.poll()
            logger.info(
                f"  - Healthy: {haproxy_status} (exit_code: {exit_code})")
            if exit_code is not None:
                logger.warning(
                    f"  - HAProxy process exited with code {exit_code}")
                try:
                    stdout, stderr = self.haproxy_process.communicate(
                        timeout=1)
                    if stderr:
                        logger.error(f"  - HAProxy stderr: {stderr[:1000]}")
                    if stdout:
                        logger.error(f"  - HAProxy stdout: {stdout[:1000]}")
                except:
                    pass

                from utils import check_haproxy_logs, get_process_info
                haproxy_logs = check_haproxy_logs()
                if haproxy_logs:
                    logger.error(
                        f"  - Recent HAProxy logs:\n{haproxy_logs[:2000]}")

        healthy_count = sum(
            1 for service in self.proxy_services if service.is_healthy())
        logger.info(
            f"Overall: {healthy_count}/{len(self.proxy_services)} proxies healthy, HAProxy: {'OK' if haproxy_status else 'FAILED'}")
        logger.info("=== END DIAGNOSIS ===")

    def restart_failed_services(self):
        logger.info("Attempting to restart failed services...")

        restarted_count = 0
        for i, service in enumerate(self.proxy_services):
            if not service.is_healthy():
                logger.info(f"Restarting proxy service {i+1}...")
                if service.restart_if_failed():
                    logger.info(f"Successfully restarted proxy service {i+1}")
                    restarted_count += 1
                else:
                    logger.warning(f"Failed to restart proxy service {i+1}")

        if self.haproxy_process and self.haproxy_process.poll() is not None:
            logger.info("Restarting HAProxy...")
            try:
                old_haproxy = self.haproxy_process
                try:
                    stdout, stderr = old_haproxy.communicate(timeout=1)
                    if stderr:
                        logger.error(
                            f"HAProxy stderr before restart: {stderr[:1000]}")
                except:
                    pass

                self.haproxy_process = subprocess.Popen([
                    'haproxy', '-f', self.haproxy_config_builder.config_file
                ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)

                time.sleep(2)
                if self.haproxy_process.poll() is None:
                    logger.info("HAProxy restarted successfully")
                    restarted_count += 1
                else:
                    stdout, stderr = self.haproxy_process.communicate()
                    logger.error(f"HAProxy restart failed")
                    logger.error(
                        f"HAProxy restart stderr: {stderr[:1000] if stderr else 'No stderr'}")

            except Exception as e:
                logger.error(f"Failed to restart HAProxy: {e}")

        logger.info(
            f"Restart operation completed. {restarted_count} services restarted.")

    def force_cleanup_ports(self):
        """Force cleanup of all ports that will be used by the proxy services."""
        logger.info("Force cleaning up ports...")

        ports_to_clean = []

        # Add all Tor and HTTP ports
        for service in self.proxy_services:
            ports_to_clean.extend([service.tor_port, service.http_port])

        # Add HAProxy ports
        ports_to_clean.extend([8080, 8404])

        from utils import ensure_port_available

        for port in ports_to_clean:
            if not ensure_port_available('127.0.0.1', port, force_kill=True):
                logger.warning(f"Could not free port {port}")
            else:
                logger.debug(f"Port {port} is now available")

        logger.info("Port cleanup completed")
