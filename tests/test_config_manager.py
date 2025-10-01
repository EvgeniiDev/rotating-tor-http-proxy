from __future__ import annotations

import pytest

from src.config_manager import (  # type: ignore[import-not-found]
    build_arg_parser,
    load_settings,
)


def test_load_settings_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    settings = load_settings()
    assert settings.tor_instances == 20
    assert settings.tor_data_dir.is_absolute()


def test_env_override_for_tor_instances(monkeypatch):
    monkeypatch.setenv("TOR_PROXY_TOR_INSTANCES", "33")
    settings = load_settings()
    assert settings.tor_instances == 33


def test_cli_override_takes_precedence(monkeypatch):
    monkeypatch.setenv("TOR_PROXY_TOR_INSTANCES", "10")
    parser = build_arg_parser()
    args = parser.parse_args(["--tor-instances", "25"])
    settings = load_settings(args)
    assert settings.tor_instances == 25


def test_invalid_env_value_raises(monkeypatch):
    monkeypatch.setenv("TOR_PROXY_TOR_INSTANCES", "not-an-int")
    with pytest.raises(ValueError):
        load_settings()
