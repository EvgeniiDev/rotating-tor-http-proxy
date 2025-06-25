import logging
import os
from typing import Dict

logger = logging.getLogger(__name__)


class ConfigManager:
    def __init__(self):
        self.data_dir = os.path.expanduser('~/tor-http-proxy/.tor_proxy/data')
        
    def create_tor_config_by_port(self, socks_port: int, subnet: str) -> Dict:
        if not subnet:
            raise ValueError("Subnet is required for Tor configuration")
        
        config_content = self.get_tor_config_by_port(socks_port, subnet)
        
        config_path = os.path.join(self.data_dir, f'torrc.{socks_port}')
        
        try:
            os.makedirs(self.data_dir, exist_ok=True)
            with open(config_path, 'w') as f:
                f.write(config_content)

            os.chmod(config_path, 0o644)
            
            logger.info(f"Created Tor config {config_path} for port {socks_port}")
            return {
                'config_path': config_path,
                'socks_port': socks_port,
            }
        except Exception as e:
            logger.error(f"Failed to create config file {config_path}: {e}")
            raise

    def get_tor_config_by_port(self, socks_port: int, subnet: str) -> str:
        if not subnet:
            raise ValueError("Subnet is required for Tor configuration")
            
        config_lines = [
            f"SocksPort 127.0.0.1:{socks_port}",
            "RunAsDaemon 0",
            f"DataDirectory {self.data_dir}/data_{socks_port}",
            "GeoIPFile /usr/share/tor/geoip",
            "GeoIPv6File /usr/share/tor/geoip6",
            "MaxCircuitDirtiness 10",
            "ExitRelay 0",
            "RefuseUnknownExits 0",
            "ClientOnly 1",
            "UseMicrodescriptors 1",
            "AvoidDiskWrites 1",
            f"ExitNodes {subnet}.0.0/16",
            "StrictNodes 1",
        ]
        
        return '\n'.join(config_lines)
