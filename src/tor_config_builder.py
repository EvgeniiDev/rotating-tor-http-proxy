import os
from typing import List


class TorConfigBuilder:
    def __init__(self, config_dir: str = '~/tor-http-proxy/tor'):
        self.config_dir = os.path.expanduser(config_dir)
        self.data_dir = os.path.expanduser('~/tor-http-proxy/data')
        os.makedirs(self.config_dir, exist_ok=True)
        os.makedirs(self.data_dir, exist_ok=True)

    def build_config(self, socks_port: int, exit_nodes: List[str]) -> str:
        data_path = os.path.join(self.data_dir, f"data_{socks_port}")
        os.makedirs(data_path, exist_ok=True)

        # Filter and validate exit nodes to ensure they are valid IPv4 addresses
        from utils import is_valid_ipv4
        valid_exit_nodes = [ip for ip in exit_nodes if is_valid_ipv4(ip)]

        if len(valid_exit_nodes) != len(exit_nodes):
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Filtered out {len(exit_nodes) - len(valid_exit_nodes)} invalid IP addresses from exit nodes")

        config_lines = [
            f"SocksPort 127.0.0.1:{socks_port}",
            f"DataDirectory {data_path}",
            "MaxCircuitDirtiness 10",
            "NewCircuitPeriod 10",
            "ExitRelay 0",
            "RefuseUnknownExits 0",
            "ClientOnly 1",
            "UseMicrodescriptors 1",
            "AvoidDiskWrites 1",
            "CircuitBuildTimeout 10",
            f"ExitNodes {','.join(exit_nodes)}",
            "StrictNodes 1",
            "EnforceDistinctSubnets 0",
        ]

        return '\n'.join(config_lines) + '\n'

    def write_config_file(self, socks_port: int, exit_nodes: List[str]) -> str:
        config_content = self.build_config(socks_port, exit_nodes)
        config_file = os.path.join(self.config_dir, f"tor_{socks_port}.conf")

        with open(config_file, 'w') as f:
            f.write(config_content)

        return config_file

    def cleanup_config_files(self):
        try:
            for config_file in os.listdir(self.config_dir):
                if config_file.startswith('tor_') and config_file.endswith('.conf'):
                    file_path = os.path.join(self.config_dir, config_file)
                    os.remove(file_path)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to cleanup Tor config files: {e}")
