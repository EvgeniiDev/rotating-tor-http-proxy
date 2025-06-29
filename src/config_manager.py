import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)


class ConfigManager:
    def __init__(self):
        self.data_dir = os.path.expanduser('~/tor-http-proxy/data')
        
    def create_tor_config_by_port(self, socks_port: int, exit_nodes: List[str]) -> Dict:
        if not exit_nodes:
            raise ValueError("Exit nodes list is required for Tor configuration")
        
        config_content = self.get_tor_config_by_port(socks_port, exit_nodes)
        
        config_path = os.path.join(self.data_dir, f'torrc.{socks_port}')
        
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(config_path, 'w') as f:
                f.write(config_content)

            os.chmod(config_path, 0o644)
            
            logger.info(f"Created Tor config {config_path} for port {socks_port} with {len(exit_nodes)} exit nodes")
            return {
                'config_path': config_path,
                'socks_port': socks_port,
                'exit_nodes_count': len(exit_nodes)
            }
        except Exception as e:
            logger.error(f"Failed to create config file {config_path}: {e}")
            raise

    def get_tor_config_by_port(self, socks_port: int, exit_nodes: List[str]) -> str:
        if not exit_nodes:
            raise ValueError("Exit nodes list is required for Tor configuration")
        
        ipv4_nodes = [ip for ip in exit_nodes if self._is_valid_ipv4(ip)]
        if not ipv4_nodes:
            raise ValueError("No valid IPv4 exit nodes provided")
        
        exit_nodes_str = ','.join(ipv4_nodes)
        
        config_lines = [
            f"SocksPort 127.0.0.1:{socks_port}",
            "RunAsDaemon 0",
            f"DataDirectory {self.data_dir}/data_{socks_port}",
            "GeoIPFile /usr/share/tor/geoip",
            "GeoIPv6File /usr/share/tor/geoip6",
            "MaxCircuitDirtiness 10",
            "NewCircuitPeriod 10",
            "CircuitBuildTimeout 15",
            "ExitRelay 0",
            "RefuseUnknownExits 0",
            "ClientOnly 1",
            "UseMicrodescriptors 1",
            "AvoidDiskWrites 1",
            f"ExitNodes {exit_nodes_str}",
            "StrictNodes 0",
            "EnforceDistinctSubnets 1",
        ]
        
        return '\n'.join(config_lines)
    
    def _is_valid_ipv4(self, ip):
        try:
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            for part in parts:
                if not (0 <= int(part) <= 255):
                    return False
            return True
        except (ValueError, AttributeError):
            return False
