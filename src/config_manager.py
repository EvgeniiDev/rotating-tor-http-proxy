import logging
import os
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class ConfigManager:
    def __init__(self, config_dir=None):
        self.base_tor_socks_port = 10000    # Tor SOCKS: 10000-19999
        self.base_tor_ctrl_port = 20000     # Tor Control: 20000-29999
        self.max_instances = 9999           # Максимально экземпляров в диапазоне
        
        home_dir = os.path.expanduser("~")
        self.config_dir = os.path.join(home_dir, ".tor_proxy", "config")
        self.data_dir = os.path.join(home_dir, ".tor_proxy", "data")
        self.log_dir = os.path.join(home_dir, ".tor_proxy", "logs")
        
    def get_tor_config(self, instance_id: int, socks_port: int, ctrl_port: int,
        subnet: str) -> str:
        if not subnet:
            raise ValueError("Subnet is required for Tor configuration")
            
        config_lines = [
            f"# Tor Instance {instance_id}",
            f"SocksPort 127.0.0.1:{socks_port}",
            f"PidFile {self.data_dir}/tor_{instance_id}.pid",
            "RunAsDaemon 0",
            f"DataDirectory {self.data_dir}/data_{instance_id}",
            "GeoIPFile /usr/share/tor/geoip",
            "GeoIPv6File /usr/share/tor/geoip6",
            "NewCircuitPeriod 10",
            "MaxCircuitDirtiness 30",
            "ExitRelay 0",
            "RefuseUnknownExits 0",
            "ClientOnly 1",
            "UseMicrodescriptors 1",
            "MaxClientCircuitsPending 16",
            "EnforceDistinctSubnets 0",
            f"ExitNodes {subnet}.0.0/16",
            "StrictNodes 1",
        ]
        
        return '\n'.join(config_lines)

    def get_port_assignment(self, instance_id: int) -> Dict:
        if instance_id < 1 or instance_id > self.max_instances:
            raise ValueError(f"Instance ID must be between 1 and {self.max_instances}")
            
        socks_port = self.base_tor_socks_port + instance_id - 1
        ctrl_port = self.base_tor_ctrl_port + instance_id - 1
        
        return {
            'socks_port': socks_port,        # 10000, 10001, 10002...
            'ctrl_port': ctrl_port,          # 20000, 20001, 20002...
        }

    def create_tor_config(self, instance_id: int, subnet: str) -> Dict:
        if not subnet:
            raise ValueError("Subnet is required for Tor configuration")
            
        ports = self.get_port_assignment(instance_id)
        config_content = self.get_tor_config(
            instance_id,
            ports['socks_port'],
            ports['ctrl_port'],
            subnet
        )
        
        config_path = os.path.join(self.config_dir, f'torrc.{instance_id}')
        
        counter = 0
        original_config_path = config_path
        while os.path.exists(config_path):
            counter += 1
            config_path = f'{original_config_path}.{counter}'
            if counter > 10:
                logger.error(f"Too many config files for instance {instance_id}, aborting")
                raise RuntimeError(f"Cannot create unique config file for instance {instance_id}")
        
        try:
            with open(config_path, 'w') as f:
                f.write(config_content)

            os.chmod(config_path, 0o644)
            
            logger.info(f"Created Tor config {config_path} for instance {instance_id}")
            return {
                'config_path': config_path,
                'socks_port': ports['socks_port'],
                'ctrl_port': ports['ctrl_port'],
            }
        except Exception as e:
            logger.error(f"Failed to create config file {config_path}: {e}")
            raise
