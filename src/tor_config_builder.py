import os
import tempfile
from typing import List, Dict, Optional, Any
from utils import is_valid_ipv4


class TorConfigBuilder:
    
    def __init__(self, data_dir: Optional[str] = None):
        self.data_dir = data_dir or os.path.expanduser('~/tor-http-proxy/data')
        
    def create_config_with_exit_nodes(self, socks_port: int, exit_nodes: List[str]) -> Dict[str, Any]:
        if not exit_nodes:
            raise ValueError("Exit nodes list is required for Tor configuration")
        
        config_content = self._generate_config_content(socks_port, exit_nodes)
        config_path = self._write_config_to_file(config_content, socks_port)
        
        return {
            'config_path': config_path,
            'socks_port': socks_port,
            'exit_nodes_count': len(exit_nodes),
            'data_directory': f"{self.data_dir}/data_{socks_port}"
        }
    
    def create_config_without_exit_nodes(self, socks_port: int) -> Dict[str, Any]:
        config_content = self._generate_config_content(socks_port, [])
        config_path = self._write_config_to_file(config_content, socks_port)
        
        return {
            'config_path': config_path,
            'socks_port': socks_port,
            'exit_nodes_count': 0,
            'data_directory': f"{self.data_dir}/data_{socks_port}"
        }
    
    def create_temporary_config(self, socks_port: int, exit_nodes: Optional[List[str]] = None) -> str:
        config_content = self._generate_config_content(socks_port, exit_nodes or [])
        
        temp_fd, temp_path = tempfile.mkstemp(suffix='.torrc', prefix=f'tor_{socks_port}_')
        with os.fdopen(temp_fd, 'w') as f:
            f.write(config_content)
        
        return temp_path
    
    def _generate_config_content(self, socks_port: int, exit_nodes: List[str]) -> str:
        config_lines = [
            f"SocksPort 127.0.0.1:{socks_port}",
            f"DataDirectory {self.data_dir}/data_{socks_port}",
            "MaxCircuitDirtiness 10",
            "NewCircuitPeriod 10",
            "ExitRelay 0",
            "RefuseUnknownExits 0",
            "ClientOnly 1",
            "UseMicrodescriptors 1",
            "AvoidDiskWrites 1",
            "FetchHidServDescriptors 0",
            "LearnCircuitBuildTimeout 0",
            "CircuitBuildTimeout 10",
        ]
        
        if exit_nodes:
            ipv4_nodes = [ip for ip in exit_nodes if is_valid_ipv4(ip)]
            if not ipv4_nodes:
                raise ValueError("No valid IPv4 exit nodes provided")
            
            exit_nodes_str = ','.join(ipv4_nodes)
            config_lines.extend([
                f"ExitNodes {exit_nodes_str}",
                "StrictNodes 1",
                "EnforceDistinctSubnets 0",
            ])
        
        return '\n'.join(config_lines)
    
    def _write_config_to_file(self, config_content: str, socks_port: int) -> str:
        os.makedirs(self.data_dir, exist_ok=True)
        config_path = os.path.join(self.data_dir, f'torrc.{socks_port}')
        
        with open(config_path, 'w') as f:
            f.write(config_content)
        
        os.chmod(config_path, 0o644)
        return config_path
    
    def cleanup_config(self, config_path: str):
        if config_path and os.path.exists(config_path):
            os.unlink(config_path)
    
    def cleanup_data_directory(self, socks_port: int):
        data_dir = os.path.join(self.data_dir, f'data_{socks_port}')
        if os.path.exists(data_dir):
            import shutil
            shutil.rmtree(data_dir, ignore_errors=True)