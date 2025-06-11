import os
import logging
import subprocess
import time
import threading
import socket
import struct
from typing import Dict

logger = logging.getLogger(__name__)


class HAProxyManager:
    def __init__(self, config_path: str = '/etc/haproxy/haproxy.cfg'):
        self.config_path = config_path
        self.socket_path = '/var/local/haproxy/haproxy.sock'
        self.base_socks_port = 10000
        self.health_check_thread = None
        self.health_check_running = False
        self.health_check_interval = 30
        self.health_check_timeout = 5
        # Убираем автоматический запуск health checks

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
        
        # Убираем health check, так как мы проверяем готовность перед добавлением
        command = f"add server {backend}/{server_name} {server_address}"
        response = self.send_command(command)
        
        if "New server registered" in response or response == "":
            time.sleep(1)
            for _ in range(3):
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

    def add_backend_instance(self, instance_id: int, socks_port: int) -> bool:
        server_name = f"tor{instance_id}"
        server_address = f"127.0.0.1:{socks_port}"
        return self.add_server("tor_socks5", server_name, server_address)

    def remove_backend_instance(self, instance_id: int) -> bool:
        server_name = f"tor{instance_id}"
        return self.remove_server("tor_socks5", server_name)

    def update_servers_dynamic(self, backend: str, target_servers: Dict[str, str]) -> bool:
        current_servers = self.get_backend_servers(backend)
        
        servers_to_remove = set(current_servers.keys()) - set(target_servers.keys())
        for server_name in servers_to_remove:
            self.remove_server(backend, server_name)

        servers_to_add = set(target_servers.keys()) - set(current_servers.keys())
        for server_name in servers_to_add:
            server_address = target_servers[server_name]
            self.add_server(backend, server_name, server_address)
            time.sleep(1)
            self.set_server_ready(backend, server_name)
        return True

    def set_server_state(self, backend: str, server_name: str, state: str) -> bool:
        valid_states = ["ready", "maint", "drain"]
        if state not in valid_states:
            return False

        commands = {
            "ready": f"enable server {backend}/{server_name}",
            "maint": f"disable server {backend}/{server_name}",
            "drain": f"set server {backend}/{server_name} state drain"
        }
        
        response = self.send_command(commands[state])
        success_indicators = ["", "enabled", "disabled", state, "already", "ok"]
        return any(indicator.lower() in response.lower() for indicator in success_indicators)

    def check_socks5_proxy(self, host: str, port: int, timeout: int = 5) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            sock.sendall(struct.pack('!BBB', 5, 1, 0))
            response = sock.recv(2)
            sock.close()
            
            if len(response) != 2:
                return False
            version, auth_method = struct.unpack('!BB', response)
            return version == 5 and auth_method == 0
        except:
            return False

    def wait_for_tor_ready(self, host: str, port: int, max_wait_time: int = 60, check_interval: int = 2) -> bool:
        """
        Ожидает готовности Tor SOCKS5 прокси перед добавлением в HAProxy
        
        Args:
            host: IP адрес хоста (обычно 127.0.0.1)
            port: Порт SOCKS5 прокси
            max_wait_time: Максимальное время ожидания в секундах
            check_interval: Интервал между проверками в секундах
            
        Returns:
            True если прокси готов, False если время ожидания истекло
        """
        start_time = time.time()
        logger.info(f"Ожидание готовности Tor прокси {host}:{port}...")
        
        while time.time() - start_time < max_wait_time:
            if self.check_socks5_proxy(host, port, timeout=3):
                logger.info(f"Tor прокси {host}:{port} готов к работе")
                return True
            
            logger.debug(f"Tor прокси {host}:{port} еще не готов, ожидание {check_interval}с...")
            time.sleep(check_interval)
        
        logger.warning(f"Tor прокси {host}:{port} не готов после {max_wait_time}с ожидания")
        return False

    def add_backend_instance_with_check(self, instance_id: int, socks_port: int, max_wait_time: int = 60) -> bool:
        """
        Добавляет backend инстанс в HAProxy только после проверки готовности Tor
        
        Args:
            instance_id: ID инстанса
            socks_port: Порт SOCKS5 прокси
            max_wait_time: Максимальное время ожидания готовности Tor
            
        Returns:
            True если инстанс успешно добавлен, False в противном случае
        """
        # Сначала ждем готовности Tor прокси
        if not self.wait_for_tor_ready("127.0.0.1", socks_port, max_wait_time):
            logger.error(f"Tor инстанс {instance_id} на порту {socks_port} не готов к работе")
            return False
        
        # Если Tor готов, добавляем в HAProxy
        if self.add_backend_instance(instance_id, socks_port):
            logger.info(f"Tor инстанс {instance_id} успешно добавлен в HAProxy backend")
            return True
        else:
            logger.error(f"Ошибка добавления Tor инстанса {instance_id} в HAProxy backend")
            return False

    def _extract_port(self, server_name: str, server_info: Dict) -> int:
        for field in ['addr', 'address', 'addr:port']:
            if field in server_info and ':' in server_info[field]:
                return int(server_info[field].rsplit(':', 1)[1])
        
        instance_id = int(server_name.replace('tor', ''))
        return instance_id + self.base_socks_port

    def health_check_daemon(self):
        """Метод health check оставляем для совместимости, но не используем автоматически"""
        while self.health_check_running:
            backends = self.get_backend_servers("tor_socks5")
            if not backends:
                time.sleep(self.health_check_interval)
                continue

            for server_name, server_info in backends.items():
                try:
                    host = '127.0.0.1'
                    port = self._extract_port(server_name, server_info)
                    is_working = self.check_socks5_proxy(host, port, self.health_check_timeout)
                    
                    current_status = None
                    for field in ['status', 'check_status', 'last_chk', 'check-status']:
                        if field in server_info:
                            current_status = server_info[field]
                            break

                    if current_status == 'no check' or not current_status:
                        state = "ready" if is_working else "maint"
                        self.set_server_state("tor_socks5", server_name, state)
                    else:
                        if is_working and current_status != 'UP':
                            self.set_server_state("tor_socks5", server_name, "ready")
                        elif not is_working and current_status != 'MAINT':
                            self.set_server_state("tor_socks5", server_name, "maint")
                except:
                    pass
            
            time.sleep(self.health_check_interval)

    def start_health_checks(self, interval: int = 30, timeout: int = 5) -> bool:
        """Ручной запуск health checks (если нужно)"""
        if self.health_check_thread and self.health_check_thread.is_alive():
            return False
            
        self.health_check_interval = interval
        self.health_check_timeout = timeout
        self.health_check_running = True
        
        backends = self.get_backend_servers("tor_socks5")
        if backends:
            for server_name in backends:
                self.set_server_ready("tor_socks5", server_name)
        
        self.health_check_thread = threading.Thread(target=self.health_check_daemon, daemon=True)
        self.health_check_thread.start()
        return True
    
    def stop_health_checks(self) -> bool:
        if not self.health_check_thread or not self.health_check_thread.is_alive():
            return False
            
        self.health_check_running = False
        self.health_check_thread.join(timeout=10)
        
        if self.health_check_thread.is_alive():
            return False
        
        self.health_check_thread = None
        return True

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

    def get_haproxy_version(self) -> str:
        response = self.send_command("show info")
        for line in response.split('\n'):
            if line.startswith('Version:'):
                return line.split(':', 1)[1].strip()
        return "unknown"
