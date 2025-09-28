#!/usr/bin/env python3
"""HAProxy Tor Pool Manager focused on config generation and service reload."""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tor_parallel_runner import TorParallelRunner

DEFAULT_FRONTEND_PORT = 9999
DEFAULT_STATS_PORT = 8404

logger = logging.getLogger(__name__)


class HAProxyTorPoolManager:
    """Writes HAProxy configuration based on Tor ports and reloads the service."""

    def __init__(
        self,
        tor_runner: TorParallelRunner,
        config_path: Optional[str] = None,
        service_name: str = "haproxy",
        pid_file: Optional[str] = None,
    ) -> None:
        self.tor_runner = tor_runner
        self.frontend_port = DEFAULT_FRONTEND_PORT
        self.stats_port = DEFAULT_STATS_PORT
        self.service_name = service_name

        default_config_path = Path("/etc/haproxy/haproxy.cfg")
        self.config_path = Path(config_path) if config_path else default_config_path

        default_pid_path = Path(pid_file) if pid_file else Path("/var/run/rotating-tor-http-proxy.pid")
        self.pid_file = default_pid_path

        self._last_applied_ports: List[int] = []
        self._last_reload_error: Optional[str] = None
        self._last_reload_timestamp: Optional[float] = None

    def check_dependencies(self) -> Tuple[bool, List[str]]:
        """Checks that haproxy and systemctl are available."""
        missing: List[str] = []
        if shutil.which("haproxy") is None:
            missing.append("haproxy")
        if shutil.which("systemctl") is None:
            missing.append("systemctl")
        if os.geteuid() != 0 and shutil.which("sudo") is None:
            missing.append("sudo")
        if not self.config_path.parent.exists():
            missing.append(str(self.config_path.parent))
        return len(missing) == 0, missing

    def generate_haproxy_config(self, tor_ports: List[int]) -> str:
        """Generates the HAProxy configuration block for the provided Tor ports."""
        backend_servers = "".join(
            f"    server tor{i} 127.0.0.1:{port} check inter 30s fall 3 rise 2 maxconn 100\n"
            for i, port in enumerate(tor_ports, 1)
        )

        if not backend_servers:
            backend_servers = (
                "    server tor_placeholder 127.0.0.1:65535 disabled  # нет активных Tor портов\n"
            )

        return (
            f"global\n"
            f"    log /dev/log local0 info\n"
            f"    maxconn 4000\n"
            f"    user haproxy\n"
            f"    group haproxy\n"
            f"    stats timeout 30s\n\n"
            f"defaults\n"
            f"    mode tcp\n"
            f"    log global\n"
            f"    option tcplog\n"
            f"    timeout connect 10s\n"
            f"    timeout client 60s\n"
            f"    timeout server 60s\n"
            f"    balance roundrobin\n"
            f"    option tcp-check\n\n"
            f"frontend tor_socks5_frontend\n"
            f"    bind *:{self.frontend_port}\n"
            f"    mode tcp\n"
            f"    default_backend tor_socks5_backend\n\n"
            f"backend tor_socks5_backend\n"
            f"    mode tcp\n"
            f"    balance roundrobin\n"
            f"    option tcp-check\n"
            f"{backend_servers}\n"
            f"frontend haproxy_stats\n"
            f"    bind *:{self.stats_port}\n"
            f"    mode http\n"
            f"    stats enable\n"
            f"    stats uri /stats\n"
            f"    stats refresh 30s\n"
            f"    stats admin if TRUE\n"
            f"    stats show-legends\n"
            f"    stats show-desc \"HAProxy Tor Pool Load Balancer\"\n"
        )

    def write_haproxy_config(self, tor_ports: List[int]) -> bool:
        """Writes the generated configuration to the configured path."""
        try:
            config_text = self.generate_haproxy_config(tor_ports)
            self.config_path.write_text(config_text)
            self.config_path.chmod(0o644)
            logger.info(
                "HAProxy конфигурация обновлена: %s активных серверов", len(tor_ports)
            )
            self._last_applied_ports = tor_ports[:]
            return True
        except PermissionError as exc:
            message = f"Нет прав для записи конфигурации {self.config_path}: {exc}"
            logger.error(message)
            self._last_reload_error = message
            return False
        except OSError as exc:
            message = f"Не удалось записать конфигурацию {self.config_path}: {exc}"
            logger.error(message)
            self._last_reload_error = message
            return False

    def validate_haproxy_config(self) -> bool:
        """Runs `haproxy -c` against the current configuration."""
        try:
            result = subprocess.run(
                ["haproxy", "-c", "-f", str(self.config_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except FileNotFoundError:
            logger.error("Команда haproxy недоступна для проверки конфигурации")
            return False
        except subprocess.SubprocessError as exc:
            logger.error("Ошибка при проверке конфигурации HAProxy: %s", exc)
            return False

        if result.returncode == 0:
            logger.debug("HAProxy конфигурация прошла проверку")
            return True

        logger.error(
            "Проверка конфигурации HAProxy завершилась ошибкой: %s", result.stderr.strip()
        )
        self._last_reload_error = result.stderr.strip()
        return False

    def reload_service(self) -> bool:
        """Reloads the configured HAProxy systemd service."""
        commands = [["systemctl", "reload", self.service_name]]
        if os.geteuid() != 0:
            commands.append(["sudo", "systemctl", "reload", self.service_name])

        last_error: Optional[str] = None

        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except FileNotFoundError:
                last_error = f"Команда не найдена: {' '.join(cmd)}"
                continue
            except subprocess.SubprocessError as exc:
                last_error = str(exc)
                continue

            if result.returncode == 0:
                logger.info("Служба %s успешно перезагружена", self.service_name)
                self._last_reload_error = None
                self._last_reload_timestamp = time.time()
                return True

            last_error = result.stderr.strip() or result.stdout.strip()

        if last_error:
            logger.error(
                "Не удалось перезагрузить службу %s: %s", self.service_name, last_error
            )
            self._last_reload_error = last_error
        else:
            logger.error(
                "Не удалось перезагрузить службу %s: неизвестная ошибка", self.service_name
            )
            self._last_reload_error = "unknown error"
        return False

    def apply_config(self, tor_ports: List[int]) -> bool:
        """Writes configuration and reloads the HAProxy service."""
        if not self.write_haproxy_config(tor_ports):
            return False
        if not self.validate_haproxy_config():
            return False
        return self.reload_service()

    def update_from_tor_runner(self) -> bool:
        """Syncs HAProxy configuration with the current Tor runner state."""
        try:
            statuses = self.tor_runner.get_statuses()
        except Exception as exc:
            logger.error("Не удалось получить статусы Tor процессов: %s", exc)
            return False

        active_ports = [port for port, state in statuses.items() if state.get("is_running")]
        if not active_ports:
            logger.warning("Нет активных Tor процессов для обновления HAProxy")

        return self.apply_config(active_ports)

    def _service_active(self) -> Optional[bool]:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", self.service_name],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError:
            return None
        except subprocess.SubprocessError:
            return None

        if result.returncode == 0:
            return result.stdout.strip() == "active"
        return False

    def get_stats(self) -> Dict[str, Any]:
        """Returns basic HAProxy/Tor statistics."""
        try:
            tor_statuses = self.tor_runner.get_statuses()
        except Exception:
            tor_statuses = {}

        running_tor = [
            port for port, state in tor_statuses.items() if state.get("is_running")
        ]

        return {
            "haproxy_running": self._service_active(),
            "tor_processes_total": len(tor_statuses),
            "tor_processes_running": len(running_tor),
            "tor_ports": running_tor,
            "frontend_port": self.frontend_port,
            "stats_port": self.stats_port,
            "config_dir": str(self.config_path.parent),
            "haproxy_config": str(self.config_path),
            "last_reload_error": self._last_reload_error,
            "last_reload_timestamp": self._last_reload_timestamp,
        }

    def is_running(self) -> bool:
        """Returns True when the managed service is active."""
        service_state = self._service_active()
        return bool(service_state)
