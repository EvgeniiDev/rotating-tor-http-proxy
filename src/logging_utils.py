from __future__ import annotations

import logging
from typing import Optional

from config_manager import TorProxySettings


def configure_logging(settings: TorProxySettings) -> None:
    """Configure logging according to runtime settings."""

    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    format_string = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    if settings.log_verbose:
        format_string = (
            "%(asctime)s %(levelname)s [%(name)s] "
            "%(process)d:%(threadName)s %(filename)s:%(lineno)d %(message)s"
        )
    logging.basicConfig(level=level, format=format_string)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return module-level logger helper."""

    return logging.getLogger(name or "rotating_tor_proxy")
