import os
from typing import List, Dict


class HAProxyConfigBuilder:
    def __init__(self, config_dir: str = '~/tor-http-proxy/haproxy'):
        self.config_dir = os.path.expanduser(config_dir)
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_file = os.path.join(self.config_dir, "haproxy.cfg")

    def build_config(self, proxy_servers: List[Dict[str, int]], listen_port: int = 8080, stats_port: int = 8404) -> str:
        config_lines = [
            "global",
            "    maxconn 4096",
            "",
            "defaults",
            "    mode http",
            "    timeout connect 5000ms",
            "    timeout client 50000ms",
            "    timeout server 50000ms",
            "    option httplog",
            "    balance roundrobin",
            "",
            f"frontend http_frontend",
            f"    bind *:{listen_port}",
            "    default_backend tor_proxies",
            "",
            f"frontend stats_frontend",
            f"    bind *:{stats_port}",
            "    stats enable",
            "    stats uri /stats",
            "    stats refresh 30s",
            "    stats hide-version",
            "    stats realm HAProxy\\ Statistics",
            "    stats admin if TRUE",
            "",
            "backend tor_proxies"
        ]

        for i, server in enumerate(proxy_servers):
            http_port = server['http_port']
            config_lines.append(
                f"    server tor_{i+1} 127.0.0.1:{http_port} check")

        return '\n'.join(config_lines) + '\n'

    def write_config_file(self, proxy_servers: List[Dict[str, int]], listen_port: int = 8080, stats_port: int = 8404) -> str:
        config_content = self.build_config(proxy_servers, listen_port, stats_port)

        with open(self.config_file, 'w') as f:
            f.write(config_content)

        return self.config_file

    def add_server(self, proxy_servers: List[Dict[str, int]], listen_port: int = 8080, stats_port: int = 8404):
        self.write_config_file(proxy_servers, listen_port, stats_port)

    def cleanup_config_file(self):
        try:
            if os.path.exists(self.config_file):
                os.remove(self.config_file)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to cleanup HAProxy config file: {e}")
