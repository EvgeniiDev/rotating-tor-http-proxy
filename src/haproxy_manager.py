#!/usr/bin/env python3

import os
import logging
import subprocess
import time
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


class HAProxyManager:

    def __init__(self, config_path: str = 'haproxy.cfg'):
        self.config_path = config_path
        self.socket_path = '/var/local/haproxy/haproxy.sock'
        self.base_http_port = 30000
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

    def add_server(self, backend: str, server_name: str, server_address: str) -> bool:
        try:
            existing_servers = self.get_backend_servers(backend)
            if server_name in existing_servers:
                logger.info(
                    f"Server {server_name} already exists in backend {backend}")
                return True

            command = f"add server {backend}/{server_name} {server_address} check"
            response = self.send_command(command)

            if "New server registered" in response or response == "":
                logger.info(f"Added server {server_name} to backend {backend}")
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

    def add_backend_instance(self, instance_id: int, http_port: int) -> bool:
        try:
            server_name = f"privoxy{instance_id}"
            server_address = f"127.0.0.1:{http_port}"

            logger.info(
                f"Adding backend instance: {server_name} -> {server_address}")

            success = self.add_server("privoxy", server_name, server_address)
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
            server_name = f"privoxy{instance_id}"

            logger.info(f"Removing backend instance: {server_name}")

            success = self.remove_server("privoxy", server_name)
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

            updated_servers = self.get_backend_servers(backend)
            logger.info(
                f"Updated servers in {backend}: {list(updated_servers.keys())}")

            return True

        except Exception as e:
            logger.error(f"Error updating backend servers dynamically: {e}")
            return False

    def get_current_backend_instances(self) -> List[Dict]:
        try:
            servers = self.get_backend_servers("privoxy")
            instances = []

            for server_name, server_info in servers.items():
                if server_name.startswith("privoxy"):
                    try:
                        instance_id = int(server_name.replace("privoxy", ""))

                        addr = server_info.get('addr', '127.0.0.1:0')
                        if ':' in addr:
                            port = int(addr.split(':')[1])
                        else:
                            port = 0

                        instances.append({
                            'id': instance_id,
                            'http_port': port,
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
                    http_port = instance.get(
                        'http_port', self.base_http_port + i)
                    server_name = f"privoxy{i}"
                    server_address = f"127.0.0.1:{http_port}"
                    target_servers[server_name] = server_address

                success = self.update_servers_dynamic(
                    "privoxy", target_servers)
                if success:
                    logger.info(
                        f"Successfully configured HAProxy with {len(instances)} instances via Runtime API")
                else:
                    logger.error("Failed to update servers via Runtime API")
                    return False
            else:
                self.update_servers_dynamic("privoxy", {})

            return True

        except Exception as e:
            logger.error(f"Failed to configure HAProxy: {e}")
            return False

    def reload_runtime_api(self, instances: List[Dict]) -> bool:
        """
        Reload HAProxy configuration using the runtime API.
        """
        return self.create_config(instances)
