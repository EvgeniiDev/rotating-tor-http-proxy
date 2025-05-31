#!/usr/bin/env python3
"""
Configuration Manager for Tor, Privoxy, and HAProxy
Handles creation and management of configuration files for all services
"""

import os
import logging
import shutil
from typing import List, Dict

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration files for Tor, Privoxy, and HAProxy services"""

    def __init__(self):
        self.base_tor_socks_port = 10000
        self.base_tor_ctrl_port = 20000
        self.base_http_port = 30000

    def create_tor_config(self, instance_id: int, instance_dir: str, subnet: str) -> str:
        """
        Create Tor configuration file for a subnet-specific instance using Python variables

        Args:
            instance_id: Unique instance identifier  
            instance_dir: Directory for Tor data
            subnet: Subnet prefix (e.g., "185.220" for /16 subnet)

        Returns:
            Path to the created configuration file
        """
        ports = self.get_instance_ports(instance_id)
        socks_port = ports['socks_port']
        ctrl_port = ports['ctrl_port']
        
        # Create subnet CIDR for ExitNodes
        subnet_cidr = f"{subnet}.0.0/16"
        
        # Create Tor data directory
        os.makedirs(instance_dir, exist_ok=True)
        os.chmod(instance_dir, 0o700)
        
        config_path = f'{instance_dir}/torrc'
        
        # Generate complete Tor configuration using Python variables
        tor_config = f"""# Tor configuration for instance {instance_id} (subnet {subnet})
DataDirectory {instance_dir}
PidFile {instance_dir}/tor.pid

# Network settings
SocksPort {socks_port}
ControlPort {ctrl_port}

# Exit node restrictions for subnet {subnet}
ExitNodes {subnet_cidr}
StrictNodes 1

# Performance and security settings
AvoidDiskWrites 1
CircuitBuildTimeout 10
LearnCircuitBuildTimeout 0
MaxCircuitDirtiness 600
NewCircuitPeriod 30
NumEntryGuards 8
"""

        try:
            with open(config_path, 'w') as f:
                f.write(tor_config)
            os.chmod(config_path, 0o600)
            
            logger.info(f"Created Tor config for subnet {subnet} instance {instance_id} at {config_path}")
            return config_path
            
        except Exception as e:
            logger.error(f"Failed to create Tor config for instance {instance_id}: {e}")
            raise

  
    def create_privoxy_config(self, instance_id: int) -> str:
        """
        Create Privoxy configuration file using Python string formatting

        Args:
            instance_id: Unique instance identifier

        Returns:
            Path to the created configuration file
        """
        ports = self.get_instance_ports(instance_id)
        tor_socks_port = ports['socks_port']
        http_port = ports['http_port']

        # Create privoxy data directory
        privoxy_data_dir = f'/var/local/privoxy/{instance_id}'
        config_path = f'{privoxy_data_dir}/config'
        
        try:
            # Create directory and set permissions
            os.makedirs(privoxy_data_dir, exist_ok=True)
            os.chmod(privoxy_data_dir, 0o755)
            
            # Generate Privoxy configuration using Python variables
            privoxy_config = f"""confdir {privoxy_data_dir}
templdir /etc/privoxy/templates
logdir /var/log/privoxy
logfile privoxy{instance_id}.log
debug 1
listen-address 127.0.0.1:{http_port}
toggle 1
enable-remote-toggle 0
enable-remote-http-toggle 0
enable-edit-actions 0
enforce-blocks 0
buffer-limit 4096
enable-proxy-authentication-forwarding 0
forward-socks5t / 127.0.0.1:{tor_socks_port} .
forwarded-connect-retries 0
accept-intercepted-requests 0
allow-cgi-request-crunching 0
split-large-forms 0
keep-alive-timeout 5
tolerance 0
"""

            with open(config_path, 'w') as f:
                f.write(privoxy_config)

            logger.info(f"Created Privoxy config for instance {instance_id} at {config_path}")
            return config_path
            
        except Exception as e:
            logger.error(f"Failed to create Privoxy config for instance {instance_id}: {e}")
            raise

    def get_privoxy_command_args(self, instance_id: int) -> list:
        """
        Get Privoxy command arguments like in original start.sh
        
        Args:
            instance_id: Unique instance identifier
            
        Returns:
            List of command arguments for Privoxy
        """
        privoxy_data_dir = f'/var/local/privoxy/{instance_id}'
        config_path = f'{privoxy_data_dir}/config'
        
        return [
            'privoxy',
            '--no-daemon',
            '--pidfile', f'{privoxy_data_dir}/privoxy.pid',
            config_path
        ]

    def get_instance_ports(self, instance_id: int) -> Dict[str, int]:
        """
        Get port configuration for a specific instance

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

    def create_haproxy_config(self, instances: List[Dict]) -> str:
        """
        Create HAProxy configuration file using Python string formatting

        Args:
            instances: List of instance configurations

        Returns:
            Path to the created configuration file
        """
        config_path = '/etc/haproxy/haproxy.cfg'
        
        try:
            # Generate complete HAProxy configuration using Python variables
            # Build server entries for each instance
            server_entries = []
            for i, instance in enumerate(instances):
                http_port = instance.get('http_port', self.base_http_port + i)
                server_entries.append(f"  server privoxy{i} 127.0.0.1:{http_port} check")
            
            servers_block = '\n'.join(server_entries)
            
            # Complete HAProxy configuration with embedded server list
            haproxy_config = f"""global
  log stdout format raw local0
  pidfile /var/local/haproxy/haproxy.pid
  maxconn 1024
  user proxy

defaults
  mode http
  log global
  log-format "%ST %B %{{+Q}}r"
  option dontlognull
  option http-server-close
  option forwardfor except 127.0.0.0/8
  option redispatch
  retries 3
  timeout http-request 10s
  timeout queue 1m
  timeout connect 10s
  timeout client 1m
  timeout server 1m
  timeout http-keep-alive 10s
  timeout check 10s
  maxconn 1024

listen stats
  bind 0.0.0.0:4444
  mode http
  log global
  maxconn 30
  timeout client 100s
  timeout server 100s
  timeout connect 100s
  timeout queue 100s
  stats enable
  stats hide-version
  stats refresh 30s
  stats show-desc Rotating Tor HTTP proxy
  stats show-legends
  stats show-node
  stats uri /

frontend main
  bind 0.0.0.0:3128
  default_backend privoxy
  mode http

backend privoxy
  balance roundrobin
  option tcp-check
{servers_block}
"""

            with open(config_path, 'w') as f:
                f.write(haproxy_config)

            logger.info(f"Created HAProxy config at {config_path} with {len(instances)} instances")
            return config_path
            
        except Exception as e:
            logger.error(f"Failed to create HAProxy config: {e}")
            raise

    def get_haproxy_command_args(self) -> list:
        """
        Get HAProxy command arguments like in original start.sh
        
        Returns:
            List of command arguments for HAProxy
        """
        return [
            'haproxy',
            '-db',
            '--',
            '/etc/haproxy/haproxy.cfg'
        ]

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

