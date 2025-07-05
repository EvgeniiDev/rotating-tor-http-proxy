import os
from typing import Dict, List
from utils import is_valid_ipv4


class ConfigManager:
    __slots__ = ('data_dir',)
    
    def __init__(self):
        self.data_dir = os.path.expanduser('~/tor-http-proxy/data')
        
    def create_tor_config_by_port(self, socks_port: int, exit_nodes: List[str]) -> Dict:
        if not exit_nodes:
            raise ValueError("Exit nodes list is required for Tor configuration")
        
        config_content = self.get_tor_config_by_port(socks_port, exit_nodes)
        config_path = os.path.join(self.data_dir, f'torrc.{socks_port}')
        
        os.makedirs(self.data_dir, exist_ok=True)
        with open(config_path, 'w') as f:
            f.write(config_content)
        os.chmod(config_path, 0o644)

        return {
            'config_path': config_path,
            'socks_port': socks_port,
            'exit_nodes_count': len(exit_nodes)
        }

    def get_tor_config_by_port(self, socks_port: int, exit_nodes: List[str]) -> str:
        if not exit_nodes:
            raise ValueError("Exit nodes list is required for Tor configuration")
        
        ipv4_nodes = [ip for ip in exit_nodes if is_valid_ipv4(ip)]
        if not ipv4_nodes:
            raise ValueError("No valid IPv4 exit nodes provided")
        
        exit_nodes_str = ','.join(ipv4_nodes)
        
        config_lines = [
            f"SocksPort 127.0.0.1:{socks_port}",
            f"DataDirectory {self.data_dir}/data_{socks_port}",
            "MaxCircuitDirtiness 10",
            "NewCircuitPeriod 10",
            "ExitRelay 0",
            "RefuseUnknownExits 0", # allow use unknown nodes as exit
            "ClientOnly 1",
            "UseMicrodescriptors 1",
            "AvoidDiskWrites 1",
            f"ExitNodes {exit_nodes_str}",
            "StrictNodes 1", # disallow to use other exit nodes. I think it doesn't work
            "EnforceDistinctSubnets 0", # allow to use exit ip from same subnet (/16)
        ]
        
        return '\n'.join(config_lines)
