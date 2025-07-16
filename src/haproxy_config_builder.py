import os
from typing import List, Dict


class HAProxyConfigBuilder:
    def __init__(self, config_dir: str = '~/tor-http-proxy/haproxy'):
        self.config_dir = os.path.expanduser(config_dir)
        os.makedirs(self.config_dir, exist_ok=True)
        self.config_file = os.path.join(self.config_dir, "haproxy.cfg")

    def build_config(self, proxy_servers: List[Dict[str, int]], listen_port: int = 8080, stats_port: int = 4444) -> str:
        config_lines = [
            "global",
            "    maxconn 30000",
            "",
            "defaults",
            "    mode http",
            "    timeout connect 10000ms",
            "    timeout client 10000ms",
            "    timeout server 10000ms",
            "    timeout queue 10000ms",
            "    option httplog",
            "    option dontlognull",
            "    option log-health-checks",
            "    option redispatch",
            "    option log-separate-errors",
            "    option abortonclose",
            "    balance roundrobin",
            "    log global",
            "",
            "frontend http_frontend",
            f"    bind 0.0.0.0:{listen_port}",
            "    default_backend tor_proxies",
            "    option forwardfor",
            "    option http-server-close",
            "",
            "listen stats",
            f"    bind :{stats_port}",
            "    stats enable",
            "    mode http",
            "    stats uri /stats",
            "    stats refresh 30s",
            "    stats realm HAProxy\\ Statistics",
            "    stats admin if TRUE",
            "",
            "backend tor_proxies",
            "    option httpchk GET /",
            "    http-check expect status 200,301,302,403,404"
        ]

        for i, server in enumerate(proxy_servers):
            http_port = server['http_port']
            config_lines.append(
                f"    server tor_{i+1} 127.0.0.1:{http_port} check inter 60s fall 5 rise 2 slowstart 60s maxconn 5")

        return '\n'.join(config_lines) + '\n'

    def write_config_file(self, proxy_servers: List[Dict[str, int]], listen_port: int = 8080, stats_port: int = 4444) -> str:
        config_content = self.build_config(
            proxy_servers, listen_port, stats_port)

        with open(self.config_file, 'w') as f:
            f.write(config_content)

        return self.config_file

    def add_server(self, proxy_servers: List[Dict[str, int]], listen_port: int = 8080, stats_port: int = 4444):
        self.write_config_file(proxy_servers, listen_port, stats_port)

    def cleanup_config_file(self):
        try:
            if os.path.exists(self.config_file):
                os.remove(self.config_file)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to cleanup HAProxy config file: {e}")
