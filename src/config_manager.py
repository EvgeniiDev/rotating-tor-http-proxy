import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ConfigManager:
    def __init__(self):
        # Упрощенные диапазоны портов: HTTP = SOCKS + 10000
        self.base_tor_socks_port = 10000    # Tor SOCKS: 10000-19999
        self.base_tor_ctrl_port = 30000     # Tor Control: 30000-39999
        self.max_instances = 9999           # Максимально экземпляров в диапазоне
        
    def get_tor_config(self, instance_id: int, socks_port: int, ctrl_port: int,
        subnet: Optional[str] = None) -> str:
        config_lines = [
            f"# Tor Instance {instance_id}",
            f"SocksPort 127.0.0.1:{socks_port}",
            f"ControlPort 127.0.0.1:{ctrl_port}",
            "HashedControlPassword 16:872860B76453A77D60CA2BB8C1A7042072093276A3D701AD684053EC4C",
            f"PidFile /var/lib/tor/tor_{instance_id}.pid",
            "RunAsDaemon 0",
            f"DataDirectory /var/lib/tor/data_{instance_id}",
            "GeoIPFile /usr/share/tor/geoip",
            "GeoIPv6File /usr/share/tor/geoip6",
            "NewCircuitPeriod 10",
            "MaxCircuitDirtiness 60",
            "UseEntryGuards 0",
            "LearnCircuitBuildTimeout 1",
            "ExitRelay 0",
            "RefuseUnknownExits 0",
            "ClientOnly 1",
            "UseMicrodescriptors 1",
            "Log notice stdout",
            "SafeLogging 1",
            "MaxClientCircuitsPending 16",
        ]

        if subnet:
            config_lines.extend([
                f"# Exit nodes in subnet {subnet}.0.0/16",
                f"ExitNodes {subnet}.0.0/16",
                "StrictNodes 1",
                ""
            ])

        return '\n'.join(config_lines)

    def get_port_assignment(self, instance_id: int) -> Dict:
        """
        Get port assignments for Tor instance

        Args:
            instance_id: Instance identifier (should be positive integer)

        Returns:
            Dictionary with port assignments
        """
        if instance_id < 1 or instance_id > self.max_instances:
            raise ValueError(f"Instance ID must be between 1 and {self.max_instances}")
            
        socks_port = self.base_tor_socks_port + instance_id - 1
        http_port = socks_port + 10000  # HTTP порт = SOCKS порт + 10000
        ctrl_port = self.base_tor_ctrl_port + instance_id - 1
        
        return {
            'socks_port': socks_port,        # 10000, 10001, 10002...
            'ctrl_port': ctrl_port,          # 30000, 30001, 30002...
            'http_port': http_port           # 20000, 20001, 20002...
        }

    def create_tor_config(self, instance_id: int, subnet: Optional[str] = None) -> Dict:
        """
        Create Tor configuration file

        Args:
            instance_id: Instance identifier
            subnet: Subnet filter

        Returns:
            Dictionary with configuration details
        """
        ports = self.get_port_assignment(instance_id)
        config_content = self.get_tor_config(
            instance_id,
            ports['socks_port'],
            ports['ctrl_port'],
            subnet
        )
        
        config_path = f'/etc/tor/torrc.{instance_id}'
        with open(config_path, 'w') as f:
            f.write(config_content)
            
        logger.info(f"Created Tor config {config_path}")
        return {
            'config_path': config_path,
            'socks_port': ports['socks_port'],
            'ctrl_port': ports['ctrl_port'],
            'http_port': ports['http_port']
        }
