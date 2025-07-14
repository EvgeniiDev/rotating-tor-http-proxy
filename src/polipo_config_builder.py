import os
from typing import List


class PolipoConfigBuilder:
    def __init__(self, config_dir: str = '~/tor-http-proxy/polipo'):
        self.config_dir = os.path.expanduser(config_dir)
        os.makedirs(self.config_dir, exist_ok=True)

    def build_config(self, http_port: int, socks_port: int) -> str:
        config_lines = [
            f"proxyAddress=127.0.0.1",
            f"proxyPort={http_port}",
            f"socksParentProxy=127.0.0.1:{socks_port}",
            "socksProxyType=socks5",
            "diskCacheRoot=",
            "localDocumentRoot=",
            "disableLocalInterface=true",
            "logSyslog=false",
            "censoredHeaders=from,accept-language,x-pad,link",
            "censorReferer=maybe"
        ]
        return '\n'.join(config_lines)

    def write_config_file(self, http_port: int, socks_port: int) -> str:
        config_content = self.build_config(http_port, socks_port)
        config_file = os.path.join(self.config_dir, f"polipo_{http_port}.conf")

        with open(config_file, 'w') as f:
            f.write(config_content)

        return config_file

    def cleanup_config_files(self):
        try:
            for config_file in os.listdir(self.config_dir):
                if config_file.startswith('polipo_') and config_file.endswith('.conf'):
                    file_path = os.path.join(self.config_dir, config_file)
                    os.remove(file_path)
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"Failed to cleanup Polipo config files: {e}")
