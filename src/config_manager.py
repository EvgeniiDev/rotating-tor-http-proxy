import os
from typing import List
from utils import is_valid_ipv4


class TorConfigBuilder:
    """
    Отвечает за генерацию конфигурационных файлов для Tor процессов.

    Логика:
    - Создает конфигурацию Tor с заданными портами и exit-нодами
    - Валидирует параметры (IPv4 адреса, порты)
    - Генерирует временные файлы конфигурации для каждого процесса
    """

    def __init__(self, data_dir: str = '~/tor-http-proxy/data'):
        self.data_dir = os.path.expanduser(data_dir)

    def build_config(self, socks_port: int, exit_nodes: List[str]) -> str:
        ipv4_nodes = [ip for ip in exit_nodes if is_valid_ipv4(ip)]
        if not ipv4_nodes:
            raise ValueError("No valid IPv4 exit nodes provided")
        exit_nodes_str = ','.join(ipv4_nodes)

        data_path = f"{self.data_dir}/data_{socks_port}"
        os.makedirs(data_path, exist_ok=True)

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
            "FetchHidServDescriptors 0",
            "LearnCircuitBuildTimeout 0",
            # "CircuitBuildTimeout 10",
            f"ExitNodes {exit_nodes_str}",
            "StrictNodes 1",
            "EnforceDistinctSubnets 0",
        ]
        return '\n'.join(config_lines)

    def build_config_without_exit_nodes(self, socks_port: int) -> str:
        # Создаём директорию для данных
        data_path = f"{self.data_dir}/data_{socks_port}"
        os.makedirs(data_path, exist_ok=True)

        config_lines = [
            f"SocksPort 127.0.0.1:{socks_port}",
            f"DataDirectory {data_path}",
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
