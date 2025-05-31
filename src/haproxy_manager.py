#!/usr/bin/env python3

import os
import logging
import subprocess
import time
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)


class HAProxyManager:

    def __init__(self, config_path: str = 'haproxy.cfg'):
        self.config_path = config_path
        self.socket_path = '/var/local/haproxy/haproxy.sock'
        self.base_socks_port = 10000
        self._socat_available = None

    def is_running(self) -> bool:
        try:
            if not os.path.exists(self.socket_path):
                logger.debug(f"HAProxy socket not found: {self.socket_path}")
                return False

            response = self.send_command("show info")
            return bool(response.strip())

        except Exception as e:
            logger.debug(f"Error checking HAProxy status: {e}")
            return False

    def send_command(self, command: str) -> str:
        try:
            if not os.path.exists(self.socket_path):
                logger.error(f"HAProxy socket not found: {self.socket_path}")
                return ""

            cmd = ['socat', 'stdio', f'unix-connect:{self.socket_path}']

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            stdout, stderr = process.communicate(
                input=command + '\n', timeout=10)

            if process.returncode == 0:
                response = stdout.strip()
                logger.debug(
                    f"HAProxy command '{command}' response: {response}")
                return response
            else:
                logger.error(f"socat failed: {stderr}")
                return ""

        except Exception as e:
            logger.error(f"Error sending HAProxy command '{command}': {e}")
            return ""

    def get_stats(self) -> Dict:
        try:
            response = self.send_command("show stat")
            if not response:
                return {}

            lines = response.split('\n')
            if not lines:
                return {}

            stats = {}
            headers = []

            for i, line in enumerate(lines):
                if line.startswith('#'):
                    headers = line[2:].split(',')
                    continue

                if not line.strip():
                    continue

                values = line.split(',')
                if len(values) >= 2:
                    pxname = values[0]
                    svname = values[1]

                    if pxname not in stats:
                        stats[pxname] = {}

                    stats[pxname][svname] = dict(zip(headers, values))

            return stats

        except Exception as e:
            logger.error(f"Error getting HAProxy stats: {e}")
            return {}

    def get_backend_servers(self, backend: str) -> Dict[str, Dict]:
        try:
            stats = self.get_stats()
            if backend in stats:
                return {k: v for k, v in stats[backend].items() if k != 'BACKEND'}
            return {}

        except Exception as e:
            logger.error(f"Error getting backend servers: {e}")
            return {}

    def set_server_ready(self, backend: str, server_name: str) -> bool:
        """
        Set a server as ready in HAProxy (changes from MAINT to READY state).
        This is useful after adding a new server since they start in MAINT state.
        """
        try:
            command = f"set server {backend}/{server_name} state ready"
            response = self.send_command(command)
            
            if response == "":  # Successful commands typically return empty string
                logger.info(f"Set server {server_name} to ready state in backend {backend}")
                return True
            else:
                logger.error(f"Failed to set server {server_name} to ready state: {response}")
                return False
                
        except Exception as e:
            logger.error(f"Error setting server to ready state: {e}")
            return False
            
    def add_server(self, backend: str, server_name: str, server_address: str) -> bool:
        try:
            existing_servers = self.get_backend_servers(backend)
            if server_name in existing_servers:
                logger.info(
                    f"Server {server_name} already exists in backend {backend}")
                return True

            # Essential check parameters for HAProxy Runtime API
            # These parameters must be explicitly specified for runtime-added servers
            check_params = "check"
            
            if backend == "tor_socks5":
                # Parse address to get host and port
                host, port_str = server_address.split(':')
                port = int(port_str)
                
                # Enhanced check params for SOCKS5 servers
                check_params = f"check inter 2s rise 1 fall 2 weight 1 maxconn 64"
                
            command = f"add server {backend}/{server_name} {server_address} {check_params}"
            response = self.send_command(command)

            if "New server registered" in response or response == "":
                logger.info(f"Added server {server_name} to backend {backend}")
                
                # Give a small delay to allow the server to be registered
                time.sleep(1)
                
                # Set server to ready state instead of MAINT
                self.set_server_ready(backend, server_name)
                
                return True
            else:
                logger.error(f"Failed to add server {server_name}: {response}")
                return False

        except Exception as e:
            logger.error(f"Error adding backend server: {e}")
            return False

    def remove_server(self, backend: str, server_name: str) -> bool:
        try:
            disable_cmd = f"disable server {backend}/{server_name}"
            self.send_command(disable_cmd)

            time.sleep(1)

            delete_cmd = f"del server {backend}/{server_name}"
            response = self.send_command(delete_cmd)

            if "Server deleted" in response or response == "":
                logger.info(
                    f"Removed server {server_name} from backend {backend}")
                return True
            else:
                logger.error(
                    f"Failed to remove server {server_name}: {response}")
                return False
        except Exception as e:
            logger.error(f"Error removing backend server: {e}")
            return False

    def add_backend_instance(self, instance_id: int, socks_port: int) -> bool:
        try:
            server_name = f"tor{instance_id}"
            server_address = f"127.0.0.1:{socks_port}"

            logger.info(
                f"Adding backend instance: {server_name} -> {server_address}")

            success = self.add_server("tor_socks5", server_name, server_address)
            if success:
                logger.info(
                    f"Successfully added backend instance {server_name}")
            else:
                logger.error(f"Failed to add backend instance {server_name}")
                
            return success

        except Exception as e:
            logger.error(f"Error adding backend instance {instance_id}: {e}")
            return False
            
    def remove_backend_instance(self, instance_id: int) -> bool:
        try:
            server_name = f"tor{instance_id}"

            logger.info(f"Removing backend instance: {server_name}")

            success = self.remove_server("tor_socks5", server_name)
            if success:
                logger.info(
                    f"Successfully removed backend instance {server_name}")
            else:
                logger.error(
                    f"Failed to remove backend instance {server_name}")

            return success

        except Exception as e:
            logger.error(f"Error removing backend instance {instance_id}: {e}")
            return False
            
    def update_servers_dynamic(self, backend: str, target_servers: Dict[str, str]) -> bool:
        try:
            current_servers = self.get_backend_servers(backend)
            logger.info(
                f"Current servers in {backend}: {list(current_servers.keys())}")
            logger.info(
                f"Target servers for {backend}: {list(target_servers.keys())}")

            servers_to_remove = set(
                current_servers.keys()) - set(target_servers.keys())
            for server_name in servers_to_remove:
                logger.info(f"Removing server {server_name}")
                self.remove_server(backend, server_name)

            servers_to_add = set(target_servers.keys()) - \
                set(current_servers.keys())
            for server_name in servers_to_add:
                server_address = target_servers[server_name]
                logger.info(f"Adding server {server_name} -> {server_address}")
                self.add_server(backend, server_name, server_address)
                
                # Ensure the server is set to ready state after adding
                time.sleep(1)  # Give time for SOCKS5 service to be available
                self.set_server_ready(backend, server_name)
                logger.info(f"Set server {server_name} to ready state")

            updated_servers = self.get_backend_servers(backend)
            logger.info(
                f"Updated servers in {backend}: {list(updated_servers.keys())}")

            return True

        except Exception as e:
            logger.error(f"Error updating backend servers dynamically: {e}")
            return False

    def test_server_health(self, backend: str, server_name: str) -> Dict[str, Any]:
        """
        Test the health of a server by checking its status and performing manual health checks.
        Returns a dictionary with health information.
        """
        try:
            # Get current server info
            servers = self.get_backend_servers(backend)
            
            if server_name not in servers:
                logger.error(f"Server {server_name} not found in backend {backend}")
                return {
                    "success": False,
                    "status": "NOT_FOUND",
                    "message": f"Server {server_name} not found in backend {backend}",
                    "last_check": None
                }
            
            server_info = servers[server_name]
            current_status = server_info.get('status', 'UNKNOWN')
            last_check = server_info.get('check_desc', 'No check info available')
            
            # Check if the server is in MAINT state and try to set it to ready
            if current_status in ('MAINT', 'DRAIN', 'no check'):
                logger.info(f"Server {server_name} is in {current_status} state. Attempting to set to ready...")
                self.set_server_ready(backend, server_name)
                time.sleep(1)  # Wait for status update
                
                # Re-check the status
                servers = self.get_backend_servers(backend)
                if server_name in servers:
                    server_info = servers[server_name]
                    current_status = server_info.get('status', 'UNKNOWN')
            
            # Check if the health check is actually being performed
            check_status = server_info.get('check_status', 'Unknown')
            check_code = server_info.get('check_code', 'Unknown')
            
            result = {
                "success": current_status in ('UP', 'READY', 'no check'),
                "status": current_status,
                "check_status": check_status,
                "check_code": check_code,
                "last_check": last_check,
                "addr": server_info.get('addr', 'Unknown')
            }
            
            return result
            
        except Exception as e:
            logger.error(f"Error testing server health: {e}")
            return {
                "success": False,
                "status": "ERROR",
                "message": str(e),
                "last_check": None
            }

    def get_current_backend_instances(self) -> List[Dict]:
        try:
            servers = self.get_backend_servers("tor_socks5")
            instances = []

            for server_name, server_info in servers.items():
                if server_name.startswith("tor"):
                    try:
                        instance_id = int(server_name.replace("tor", ""))

                        addr = server_info.get('addr', '127.0.0.1:0')
                        if ':' in addr:
                            port = int(addr.split(':')[1])
                        else:
                            port = 0

                        instances.append({
                            'id': instance_id,
                            'socks_port': port,
                            'server_name': server_name,
                            'status': server_info.get('status', 'UNKNOWN')
                        })
                    except (ValueError, KeyError) as e:
                        logger.warning(
                            f"Failed to parse server info for {server_name}: {e}")

            return sorted(instances, key=lambda x: x['id'])

        except Exception as e:
            logger.error(f"Error getting current backend instances: {e}")
            return []

    def create_config(self, instances: List[Dict]) -> bool:
        try:
            # Simply assume HAProxy is running since it's started externally
            if not os.path.exists(self.socket_path):
                logger.error(
                    "HAProxy socket not available. Make sure HAProxy is running with socket enabled.")
                return False

            if instances:
                target_servers = {}
                for i, instance in enumerate(instances):
                    socks_port = instance.get(
                        'socks_port', self.base_socks_port + i)
                    server_name = f"tor{i}"
                    server_address = f"127.0.0.1:{socks_port}"
                    target_servers[server_name] = server_address

                success = self.update_servers_dynamic(
                    "tor_socks5", target_servers)
                if success:
                    logger.info(
                        f"Successfully configured HAProxy with {len(instances)} SOCKS5 instances via Runtime API")
                else:
                    logger.error("Failed to update servers via Runtime API")
                    return False
            else:
                self.update_servers_dynamic("tor_socks5", {})

            return True

        except Exception as e:
            logger.error(f"Failed to configure HAProxy: {e}")
            return False
