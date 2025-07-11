import os
import tempfile
from typing import List
from utils import is_valid_ipv4


class TorConfigManager:
    """Отвечает за создание конфигурации Tor"""
    
    def __init__(self, data_dir: str = None):
        self.data_dir = data_dir or os.path.expanduser('~/tor-http-proxy/data')
        
    def create_config(self, port: int, exit_nodes: List[str] = None) -> str:
        """Создает конфигурацию Tor для указанного порта"""
        if exit_nodes:
            return self._create_config_with_exit_nodes(port, exit_nodes)
        else:
            return self._create_config_without_exit_nodes(port)
    
    def _create_config_with_exit_nodes(self, port: int, exit_nodes: List[str]) -> str:
        """Создает конфигурацию с указанными выходными нодами"""
        if not exit_nodes:
            raise ValueError("Exit nodes list is required for Tor configuration")
        
        ipv4_nodes = [ip for ip in exit_nodes if is_valid_ipv4(ip)]
        if not ipv4_nodes:
            raise ValueError("No valid IPv4 exit nodes provided")
        
        exit_nodes_str = ','.join(ipv4_nodes)
        
        config_lines = [
            f"SocksPort 127.0.0.1:{port}",
            f"DataDirectory {self.data_dir}/data_{port}",
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
            f"ExitNodes {exit_nodes_str}",
            "StrictNodes 1",
            "EnforceDistinctSubnets 0",
        ]
        
        return '\n'.join(config_lines)
    
    def _create_config_without_exit_nodes(self, port: int) -> str:
        """Создает конфигурацию без указания выходных нод"""
        config_lines = [
            f"SocksPort 127.0.0.1:{port}",
            f"DataDirectory {self.data_dir}/data_{port}",
            "MaxCircuitDirtiness 10",
            "NewCircuitPeriod 10", 
            "ExitRelay 0",
            "ClientOnly 1",
            "UseMicrodescriptors 1",
            "AvoidDiskWrites 1",
            "FetchHidServDescriptors 0",
            "LearnCircuitBuildTimeout 0",
            "CircuitBuildTimeout 10",
        ]
        
        return '\n'.join(config_lines)
    
    def save_config_to_file(self, port: int, exit_nodes: List[str] = None) -> str:
        """Сохраняет конфигурацию в файл и возвращает путь к файлу"""
        config_content = self.create_config(port, exit_nodes)
        
        temp_fd, config_file = tempfile.mkstemp(suffix='.torrc', prefix=f'tor_{port}_')
        
        with os.fdopen(temp_fd, 'w') as f:
            f.write(config_content)
            
        return config_file