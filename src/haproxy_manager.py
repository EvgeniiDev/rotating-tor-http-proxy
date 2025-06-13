import os
import logging
import subprocess
import time
from typing import Dict

logger = logging.getLogger(__name__)


class HAProxyManager:
    def __init__(self, config_path: str = '/etc/haproxy/haproxy.cfg'):
        self.config_path = config_path
        self.socket_path = '/var/local/haproxy/haproxy.sock'

    def is_running(self) -> bool:
        if not os.path.exists(self.socket_path):
            return False
        response = self.send_command("show info")
        return bool(response.strip())

    def send_command(self, command: str) -> str:
        if not os.path.exists(self.socket_path):
            return ""
        
        try:
            process = subprocess.Popen(
                ['socat', 'stdio', f'unix-connect:{self.socket_path}'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            stdout, _ = process.communicate(input=command + '\n', timeout=10)
            return stdout.strip() if process.returncode == 0 else ""
        except:
            return ""

    def get_stats(self) -> Dict:
        response = self.send_command("show stat")
        if not response:
            return {}

        stats = {}
        headers = []
        
        for line in response.split('\n'):
            if line.startswith('#'):
                headers = line[2:].split(',')
            elif line.strip():
                values = line.split(',')
                if len(values) >= 2:
                    pxname, svname = values[0], values[1]
                    if pxname not in stats:
                        stats[pxname] = {}
                    stats[pxname][svname] = dict(zip(headers, values))
        return stats

    def get_backend_servers(self, backend: str) -> Dict[str, Dict]:
        stats = self.get_stats()
        return {k: v for k, v in stats.get(backend, {}).items() if k != 'BACKEND'}

    def _server_command(self, backend: str, server_name: str, action: str) -> bool:
        command = f"{action} server {backend}/{server_name}"
        response = self.send_command(command)
        success_indicators = ["", "enabled", "disabled", "already", "ok", action.split()[0]]
        return any(indicator.lower() in response.lower() for indicator in success_indicators)

    def set_server_ready(self, backend: str, server_name: str) -> bool:
        return self._server_command(backend, server_name, "enable")

    def add_server(self, backend: str, server_name: str, server_address: str) -> bool:
        existing_servers = self.get_backend_servers(backend)
        if server_name in existing_servers:
            return True
        
        # Add server with health checks enabled
        command = f"add server {backend}/{server_name} {server_address} check"
        response = self.send_command(command)
        
        if "New server registered" in response or response == "":
            time.sleep(2)  # Give more time for health checks
            # Enable the server after health checks pass
            for _ in range(5):  # More attempts for health checks
                if self.set_server_ready(backend, server_name):
                    break
                time.sleep(1)
            return True
        return False

    def remove_server(self, backend: str, server_name: str) -> bool:
        self.send_command(f"disable server {backend}/{server_name}")
        time.sleep(1)
        response = self.send_command(f"del server {backend}/{server_name}")
        return "Server deleted" in response or response == ""

    def add_http_backend_instance(self, instance_id: int, http_port: int) -> bool:
        """Add HTTP proxy server to tor_http backend"""
        server_name = f"http{instance_id}"
        server_address = f"127.0.0.1:{http_port}"
        return self.add_server("tor_http", server_name, server_address)

    def remove_http_backend_instance(self, instance_id: int) -> bool:
        """Remove HTTP proxy server from tor_http backend"""
        server_name = f"http{instance_id}"
        return self.remove_server("tor_http", server_name)

    def get_server_info(self, backend: str, server_name: str) -> Dict:
        command = f"show servers state {backend}"
        response = self.send_command(command)
        
        if not response:
            stats = self.get_backend_servers(backend)
            return stats.get(server_name, {})
        
        for line in response.split('\n'):
            if line.strip() and server_name in line:
                parts = line.split()
                if len(parts) >= 10:
                    return {
                        'name': server_name,
                        'address': parts[3] if len(parts) > 3 else 'unknown',
                        'status': parts[4] if len(parts) > 4 else 'unknown',
                        'weight': parts[5] if len(parts) > 5 else 'unknown',
                        'check_status': parts[6] if len(parts) > 6 else 'unknown'
                    }
        return {}
