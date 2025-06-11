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
        self.base_http_port = 10000


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


    def add_server(self, backend: str, server_name: str, server_address: str) -> bool:
        existing_servers = self.get_backend_servers(backend)
        if server_name in existing_servers:
            return True
        
        # Добавляем сервер с настройками для health check
        # check - включает health checking
        # inter 30s - интервал проверки 30 секунд  
        # rise 2 - считать UP после 2 успешных проверок
        # fall 3 - считать DOWN после 3 неудачных проверок
        server_params = f"{server_address} check inter 30s rise 2 fall 3"
        command = f"add server {backend}/{server_name} {server_params}"
        response = self.send_command(command)

        if "New server registered" in response or response == "":
            logger.info(f"Сервер {server_name} добавлен с health checking")
            return True
        return False

    def remove_server(self, backend: str, server_name: str) -> bool:
        self.send_command(f"disable server {backend}/{server_name}")
        time.sleep(1)
        response = self.send_command(f"del server {backend}/{server_name}")
        return "Server deleted" in response or response == ""

    def add_backend_instance(self, instance_id: int, http_port: int) -> bool:
        server_name = f"tor{instance_id}"

        server_address = f"127.0.0.1:{http_port}"
        return self.add_server("tor_http_tunnel", server_name, server_address)

    def remove_backend_instance(self, instance_id: int) -> bool:
        server_name = f"tor{instance_id}"
        return self.remove_server("tor_http_tunnel", server_name)


    def _extract_port(self, server_name: str, server_info: Dict) -> int:
        for field in ['addr', 'address', 'addr:port']:
            if field in server_info and ':' in server_info[field]:
                return int(server_info[field].rsplit(':', 1)[1])

        instance_id = int(server_name.replace('tor', ''))
        return instance_id + self.base_http_port


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
                    return {                        'name': server_name,
                        'address': parts[3] if len(parts) > 3 else 'unknown',
                        'status': parts[4] if len(parts) > 4 else 'unknown',
                        'weight': parts[5] if len(parts) > 5 else 'unknown',
                        'check_status': parts[6] if len(parts) > 6 else 'unknown'
                    }
        return {}

    def get_haproxy_version(self) -> str:
        response = self.send_command("show info")
        for line in response.split('\n'):
            if line.startswith('Version:'):
                return line.split(':', 1)[1].strip()
        return "unknown"

    def get_servers_health_status(self, backend: str = "tor_http_tunnel") -> Dict[str, Dict]:
        servers = self.get_backend_servers(backend)
        health_status = {}
        
        for server_name, server_info in servers.items():
            status = server_info.get('status', 'UNKNOWN')
            check_status = server_info.get('check_status', 'UNK')
            
            is_healthy = status in ['UP', 'OPEN'] and 'L4OK' in check_status
            
            health_status[server_name] = {
                'healthy': is_healthy,
                'status': status,
                'check_status': check_status,
                'port': self._extract_port(server_name, server_info),
                'last_chk': server_info.get('lastchk', ''),
                'downtime': server_info.get('downtime', '0'),
                'check_duration': server_info.get('check_duration', '')
            }
            
        return health_status

    def get_backend_health_summary(self, backend: str = "tor_http_tunnel") -> Dict:
        health_status = self.get_servers_health_status(backend)
        
        total_servers = len(health_status)
        healthy_servers = sum(1 for status in health_status.values() if status['healthy'])
        
        return {
            'backend': backend,
            'total_servers': total_servers,
            'healthy_servers': healthy_servers,
            'unhealthy_servers': total_servers - healthy_servers,
            'health_percentage': (healthy_servers / total_servers * 100) if total_servers > 0 else 0,
            'servers': health_status
        }
