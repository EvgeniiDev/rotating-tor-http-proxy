#!/usr/bin/env python3
"""
Configuration Manager for Tor, Privoxy, and HAProxy
Handles creation and management of configuration files for all services
Refactored to use HAProxyManager for all HAProxy operations
"""

import os
import logging
import shutil
import subprocess
import signal
import time
import json
import platform
from typing import List, Dict, Optional
from haproxy_manager import HAProxyManager

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration files for Tor, Privoxy, and HAProxy services"""
    
    def __init__(self):
        self.base_tor_socks_port = 10000
        self.base_tor_ctrl_port = 20000
        self.base_http_port = 30000
        
        # Initialize HAProxy manager
        self.haproxy_manager = HAProxyManager()

    def get_tor_config(self, instance_id: int, socks_port: int, ctrl_port: int, 
                       exit_country: Optional[str] = None, subnet: Optional[str] = None) -> str:
        """
        Generate Tor configuration content

        Args:
            instance_id: Unique instance identifier
            socks_port: SOCKS proxy port
            ctrl_port: Control port
            exit_country: Exit country code (e.g., 'US', 'GB')
            subnet: Subnet filter (e.g., '185.220' for /16 subnet)

        Returns:
            Tor configuration content as string
        """
        config_lines = [
            f"# Tor Instance {instance_id}",
            "AvoidDiskWrites 1",
            "GeoIPExcludeUnknown 1",
            f"SocksPort 0.0.0.0:{socks_port}",
            f"ControlPort 0.0.0.0:{ctrl_port}",
            "HashedControlPassword 16:872860B76453A77D60CA2BB8C1A7042072093276A3D701AD684053EC4C",
            "PidFile /var/lib/tor/tor.pid",
            "RunAsDaemon 1",
            "User tor",
            "DataDirectory /var/lib/tor",
            "GeoIPFile /usr/share/tor/geoip",
            "GeoIPv6File /usr/share/tor/geoip6",
            "Log notice stdout",
            ""
        ]

        # Add exit country restriction if specified
        if exit_country:
            config_lines.extend([
                f"ExitNodes {{{exit_country}}}",
                "StrictNodes 1",
                ""
            ])

        # Add subnet-based exit node selection if specified
        if subnet:
            # For /16 subnets like "185.220", we want exits in that range
            config_lines.extend([
                f"# Exit nodes in subnet {subnet}.0.0/16",
                f"ExitNodes ${{" + subnet + "0000-" + subnet + "FFFF}}",
                "StrictNodes 1",
                ""
            ])

        return '\n'.join(config_lines)

    def get_privoxy_config(self, socks_port: int, http_port: int) -> str:
        """
        Generate Privoxy configuration content

        Args:
            socks_port: Tor SOCKS port to forward to
            http_port: HTTP port for Privoxy to listen on

        Returns:
            Privoxy configuration content as string
        """
        return f"""# Privoxy Configuration
user-manual /usr/share/doc/privoxy/user-manual
confdir /etc/privoxy
logdir /var/log/privoxy
actionsfile match-all.action
actionsfile default.action
filterfile default.filter
logfile logfile
listen-address 0.0.0.0:{http_port}
forward-socks5t / 127.0.0.1:{socks_port} .
"""

    def create_tor_config(self, instance_id: int, exit_country: Optional[str] = None, 
                         subnet: Optional[str] = None) -> Dict:
        """
        Create Tor configuration file

        Args:
            instance_id: Instance identifier
            exit_country: Exit country code
            subnet: Subnet filter

        Returns:
            Dictionary with configuration details
        """
        ports = self.get_port_assignment(instance_id)
        config_content = self.get_tor_config(
            instance_id, 
            ports['socks_port'], 
            ports['ctrl_port'],
            exit_country,
            subnet
        )
        
        config_path = f'/etc/tor/torrc.{instance_id}'
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        logger.info(f"Created Tor config {config_path}")
        return {
            'config_path': config_path,
            'socks_port': ports['socks_port'],
            'ctrl_port': ports['ctrl_port']
        }

    def create_privoxy_config(self, instance_id: int, tor_socks_port: int) -> Dict:
        """
        Create Privoxy configuration file

        Args:
            instance_id: Instance identifier
            tor_socks_port: Tor SOCKS port to connect to

        Returns:
            Dictionary with configuration details
        """
        http_port = self.base_http_port + instance_id - 1
        config_content = self.get_privoxy_config(tor_socks_port, http_port)
        
        config_path = f'/etc/privoxy/config.{instance_id}'
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        logger.info(f"Created Privoxy config {config_path}")
        return {
            'config_path': config_path,
            'http_port': http_port
        }

    def create_configs(self, instances: List[Dict]) -> List[Dict]:
        """
        Create all configuration files for given instances

        Args:
            instances: List of instance configurations

        Returns:
            List of created configurations with paths and ports
        """
        configs = []
        
        for instance in instances:
            instance_id = instance['id']
            exit_country = instance.get('exit_country')
            subnet = instance.get('subnet')
            
            # Create Tor config
            tor_config = self.create_tor_config(instance_id, exit_country, subnet)
            
            # Create Privoxy config
            privoxy_config = self.create_privoxy_config(
                instance_id, 
                tor_config['socks_port']
            )
            
            configs.append({
                'id': instance_id,
                'tor_config': tor_config['config_path'],
                'privoxy_config': privoxy_config['config_path'],
                'socks_port': tor_config['socks_port'],
                'ctrl_port': tor_config['ctrl_port'],
                'http_port': privoxy_config['http_port'],
                'exit_country': exit_country,
                'subnet': subnet
            })
        
        logger.info(f"Created configurations for {len(configs)} instances")
        return configs

    def get_port_assignment(self, instance_id: int) -> Dict:
        """
        Get port assignment for an instance

        Args:
            instance_id: Instance identifier

        Returns:
            Dictionary containing port assignments
        """
        return {
            'socks_port': self.base_tor_socks_port + instance_id - 1,
            'ctrl_port': self.base_tor_ctrl_port + instance_id - 1,
            'http_port': self.base_http_port + instance_id - 1
        }

    def cleanup_configs(self):
        """
        Clean up configuration files

        Removes all Tor and Privoxy configuration files created by this manager
        """
        try:
            for config_dir in ['/etc/tor', '/etc/privoxy']:
                if not os.path.exists(config_dir):
                    continue

                for filename in os.listdir(config_dir):
                    if filename.startswith('.'):
                        continue

                    file_path = os.path.join(config_dir, filename)
                    if os.path.isfile(file_path) and (
                        filename.startswith('torrc.') or
                        filename.startswith('config.')
                    ):
                        try:
                            os.remove(file_path)
                            logger.info(f"Removed config file: {file_path}")
                        except Exception as e:
                            logger.error(f"Error removing config file {file_path}: {e}")

        except Exception as e:
            logger.error(f"Error during config cleanup: {e}")

    def validate_subnet(self, subnet: str) -> bool:
        """
        Validate subnet format

        Args:
            subnet: Subnet string (e.g., "185.220" for /16 subnet)

        Returns:
            True if valid, False otherwise
        """
        try:
            parts = subnet.split('.')
            # For /16 subnets, we expect 2 parts (e.g., "185.220")
            if len(parts) != 2:
                return False

            for part in parts:
                if not part.isdigit() or not (0 <= int(part) <= 255):
                    return False

            return True
        except Exception:
            return False

    def get_backend_servers(self, backend: str) -> Dict[str, Dict]:
        """Get backend servers via Runtime API (delegated to HAProxyManager)"""
        return self.haproxy_manager.get_backend_servers(backend)
