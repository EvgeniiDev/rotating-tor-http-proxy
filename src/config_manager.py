from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path


_TOR_ENV_KEY = "TOR_PROXY_TOR_INSTANCES"
_MIN_TOR_INSTANCES = 1
_MAX_TOR_INSTANCES = 400


def _expand_path(value: Path | str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _normalize_log_level(level: str) -> str:
    normalized = level.upper()
    allowed = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
    if normalized not in allowed:
        raise ValueError(f"Unsupported log level: {level}")
    return normalized


def _validate_tor_instances(value: int) -> int:
    if not (_MIN_TOR_INSTANCES <= value <= _MAX_TOR_INSTANCES):
        raise ValueError(
            f"tor_instances must be between {_MIN_TOR_INSTANCES} and {_MAX_TOR_INSTANCES}, got {value}"
        )
    return value


@dataclass
class TorProxySettings:
    """Runtime configuration with only tor_instances override support."""

    tor_instances: int = 20
    tor_base_port: int = 10_000
    tor_max_port: int = 10_799
    tor_start_batch: int = 20
    tor_start_timeout_seconds: float = 90.0
    tor_start_retries: int = 2
    tor_start_retry_delay_seconds: float = 5.0
    tor_data_dir: Path = Path("./data")

    frontend_port: int = 9_999

    health_check_url: str = "https://httpbin.org/ip"
    steam_health_url: str = "https://steamcommunity.com/market/listings/730/AK-47"
    health_timeout_seconds: float = 10.0
    health_retries: int = 3
    health_interval_seconds: float = 60.0

    log_level: str = "INFO"
    log_verbose: bool = False

    exit_nodes_per_instance: int = 0
    exit_nodes_max: int = 0

    systemctl_binary: str = "systemctl"

    def __post_init__(self) -> None:
        self.tor_instances = _validate_tor_instances(int(self.tor_instances))
        self.tor_data_dir = _expand_path(self.tor_data_dir)
        self.log_level = _normalize_log_level(self.log_level)
        if self.tor_max_port < self.tor_base_port:
            raise ValueError("tor_max_port must be greater than or equal to tor_base_port")
        if self.tor_start_batch <= 0:
            raise ValueError("tor_start_batch must be positive")
        if self.tor_start_timeout_seconds <= 0:
            raise ValueError("tor_start_timeout_seconds must be positive")
        if self.tor_start_retries < 0:
            raise ValueError("tor_start_retries must be non-negative")
        if self.tor_start_retry_delay_seconds < 0:
            raise ValueError("tor_start_retry_delay_seconds must be non-negative")

    def with_tor_instances(self, value: int) -> "TorProxySettings":
        return TorProxySettings(
            tor_instances=_validate_tor_instances(value),
            tor_base_port=self.tor_base_port,
            tor_max_port=self.tor_max_port,
            tor_start_batch=self.tor_start_batch,
            tor_start_timeout_seconds=self.tor_start_timeout_seconds,
            tor_start_retries=self.tor_start_retries,
            tor_start_retry_delay_seconds=self.tor_start_retry_delay_seconds,
            tor_data_dir=self.tor_data_dir,
            frontend_port=self.frontend_port,
            health_check_url=self.health_check_url,
            steam_health_url=self.steam_health_url,
            health_timeout_seconds=self.health_timeout_seconds,
            health_retries=self.health_retries,
            health_interval_seconds=self.health_interval_seconds,
            log_level=self.log_level,
            log_verbose=self.log_verbose,
            exit_nodes_per_instance=self.exit_nodes_per_instance,
            exit_nodes_max=self.exit_nodes_max,
            systemctl_binary=self.systemctl_binary,
        )


def load_settings(args: argparse.Namespace | None = None) -> TorProxySettings:
    settings = TorProxySettings()

    env_value = os.getenv(_TOR_ENV_KEY)
    if env_value is not None:
        try:
            settings = settings.with_tor_instances(int(env_value))
        except ValueError as error:
            raise ValueError(f"Invalid {_TOR_ENV_KEY} value: {env_value}") from error

    if args and getattr(args, "tor_instances", None) is not None:
        settings = settings.with_tor_instances(args.tor_instances)

    return settings


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rotating Tor HTTP proxy controller")
    parser.add_argument(
        "--tor-instances",
        dest="tor_instances",
        type=int,
        default=None,
        help="Number of Tor instances to launch",
    )
    return parser
