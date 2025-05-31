import os
import logging
from typing import List, Dict, Optional
from haproxy_manager import HAProxyManager

logger = logging.getLogger(__name__)


class ConfigManager:
    """Manages configuration files for Tor and HAProxy services"""

    def __init__(self):
        self.base_tor_socks_port = 10000
        self.base_tor_ctrl_port = 20000
        self.haproxy_manager = HAProxyManager()

    def get_tor_config(self, instance_id: int, socks_port: int, ctrl_port: int,
                       subnet: Optional[str] = None) -> str:
        """
        Generate Tor configuration content

        Args:
            instance_id: Unique instance identifier
            socks_port: SOCKS proxy port
            ctrl_port: Control port
            subnet: Subnet filter (e.g., '185.220' for /16 subnet)

        Returns:
            Tor configuration content as string
        """
        config_lines = [
            f"# Tor Instance {instance_id}",
            "AvoidDiskWrites 1",
            "GeoIPExcludeUnknown 1",
            f"SocksPort 127.0.0.1:{socks_port}",
            f"ControlPort 127.0.0.1:{ctrl_port}",
            "HashedControlPassword 16:872860B76453A77D60CA2BB8C1A7042072093276A3D701AD684053EC4C",
            f"PidFile /var/lib/tor/tor_{instance_id}.pid",
            "RunAsDaemon 0",
            f"DataDirectory /var/lib/tor/data_{instance_id}",
            "GeoIPFile /usr/share/tor/geoip",
            "GeoIPv6File /usr/share/tor/geoip6",
            "Log notice stdout",
        ]

        # Add subnet-based exit node selection if specified
        if subnet:
            # For /16 subnets like "185.220", we want exits in that range
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
        return {
            'socks_port': self.base_tor_socks_port + instance_id - 1,
            'ctrl_port': self.base_tor_ctrl_port + instance_id - 1
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
            'ctrl_port': ports['ctrl_port']
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
            subnet = instance.get('subnet')

            # Create Tor config
            tor_config = self.create_tor_config(instance_id, subnet)

            configs.append({
                'id': instance_id,
                'tor_config': tor_config['config_path'],
                'socks_port': tor_config['socks_port'],
                'ctrl_port': tor_config['ctrl_port'],
                'subnet': subnet
            })

        logger.info(f"Created configurations for {len(configs)} instances")
        return configs

    def cleanup_configs(self):
        """
        Clean up configuration files

        Removes all Tor configuration files created by this manager
        """
        try:
            config_dir = '/etc/tor'
            if not os.path.exists(config_dir):
                return

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
                            logger.error(
                                f"Error removing config file {file_path}: {e}")

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

    def create_secure_default_torrc(self) -> str:
        """
        Create a secure default Tor configuration that addresses common security issues
        
        Returns:
            Path to the created default torrc file
        """
        config_content = """# Secure Default Tor Configuration
# Security: Bind only to localhost to prevent external access
SocksPort 127.0.0.1:9050
ControlPort 127.0.0.1:9051

# Authentication
HashedControlPassword 16:0E845EB82BCDB7BF604C82C0D8A5E4A4D44EDB7360098EBE6B099505D3
CookieAuthentication 1
CookieAuthFileGroupReadable 1

# Basic settings
RunAsDaemon 0
ClientOnly 1
ExitRelay 0

# Performance and privacy
AvoidDiskWrites 1
NewCircuitPeriod 30
MaxCircuitDirtiness 300
UseEntryGuards 0
LearnCircuitBuildTimeout 1

# Logging
Log notice stdout

# Data directory
DataDirectory /var/lib/tor
PidFile /var/lib/tor/tor.pid
"""
        
        config_path = '/etc/tor/torrc.secure'
        try:
            with open(config_path, 'w') as f:
                f.write(config_content)
            logger.info(f"Created secure default Tor config: {config_path}")
            return config_path
        except Exception as e:
            logger.error(f"Failed to create secure default Tor config: {e}")
            raise

    def fix_torrc_security_issues(self, torrc_path: str) -> bool:
        """
        Fix common security issues in existing torrc files
        
        Args:
            torrc_path: Path to the torrc file to fix
            
        Returns:
            True if fixes were applied, False otherwise
        """
        try:
            if not os.path.exists(torrc_path):
                logger.warning(f"Torrc file not found: {torrc_path}")
                return False
                
            with open(torrc_path, 'r') as f:
                content = f.read()
            
            # Backup original
            backup_path = f"{torrc_path}.backup"
            with open(backup_path, 'w') as f:
                f.write(content)
            logger.info(f"Created backup: {backup_path}")
            
            # Apply security fixes
            lines = content.split('\n')
            fixed_lines = []
            
            for line in lines:
                line = line.strip()
                
                # Skip empty lines and comments
                if not line or line.startswith('#'):
                    fixed_lines.append(line)
                    continue
                
                # Fix SocksPort binding to 0.0.0.0
                if line.startswith('SocksPort') and '0.0.0.0:' in line:
                    port = line.split(':')[-1]
                    fixed_line = f"SocksPort 127.0.0.1:{port}"
                    logger.info(f"Fixed SocksPort: {line} -> {fixed_line}")
                    fixed_lines.append(fixed_line)
                    continue
                
                # Fix ControlPort binding to 0.0.0.0
                if line.startswith('ControlPort') and '0.0.0.0:' in line:
                    port = line.split(':')[-1]
                    fixed_line = f"ControlPort 127.0.0.1:{port}"
                    logger.info(f"Fixed ControlPort: {line} -> {fixed_line}")
                    fixed_lines.append(fixed_line)
                    continue
                
                # Remove User directive if already running as that user
                if line.startswith('User '):
                    logger.info(f"Removed User directive: {line}")
                    fixed_lines.append(f"# {line} # Removed to avoid conflicts")
                    continue
                
                # Keep other lines as-is
                fixed_lines.append(line)
            
            # Add security enhancements if not present
            security_options = [
                "CookieAuthentication 1",
                "CookieAuthFileGroupReadable 1", 
                "AvoidDiskWrites 1"
            ]
            
            for option in security_options:
                option_key = option.split()[0]
                if not any(line.strip().startswith(option_key) for line in fixed_lines):
                    fixed_lines.append(option)
                    logger.info(f"Added security option: {option}")
            
            # Write fixed configuration
            fixed_content = '\n'.join(fixed_lines)
            with open(torrc_path, 'w') as f:
                f.write(fixed_content)
            
            logger.info(f"Applied security fixes to: {torrc_path}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to fix torrc security issues: {e}")
            return False
