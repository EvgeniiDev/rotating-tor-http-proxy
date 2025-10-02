from __future__ import annotations

import json
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import aiohttp
import asyncio
from aiohttp_socks import ProxyConnector
from aiohttp import ClientTimeout

from .exceptions import TorHealthCheckError, TorInstanceError
from .logging_utils import get_logger
from .utils import ensure_directory

_TOR_STARTUP_GRACE_SECONDS = 45


@dataclass
class TorRuntimeMetadata:
    socks_port: int
    config_path: Path
    data_dir: Path
    log_path: Path
    pid_file: Path


@dataclass
class TorInstance:
    instance_id: int
    tor_binary: str
    metadata: TorRuntimeMetadata
    exit_nodes: list[str]
    health_check_url: str
    health_timeout_seconds: float
    max_health_retries: int
    startup_timeout_seconds: float = field(default=_TOR_STARTUP_GRACE_SECONDS)
    process: Optional[subprocess.Popen] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self._logger = get_logger(f"tor[{self.instance_id}]")
        ensure_directory(self.metadata.data_dir)
        ensure_directory(self.metadata.config_path.parent)
        ensure_directory(self.metadata.pid_file.parent)

    @property
    def config_path(self) -> Path:
        return self.metadata.config_path

    @property
    def data_dir(self) -> Path:
        return self.metadata.data_dir

    @property
    def log_path(self) -> Path:
        return self.metadata.log_path

    @property
    def socks_port(self) -> int:
        return self.metadata.socks_port

    @property
    def pid_file(self) -> Path:
        return self.metadata.pid_file

    def create_config(self) -> None:
        lines: list[str] = [
            f"SocksPort 127.0.0.1:{self.socks_port}",
            f"DataDirectory {self.data_dir}",
            f"Log notice file {self.log_path}",
            f"PidFile {self.pid_file}",
            "AvoidDiskWrites 1",
            "MaxCircuitDirtiness 60",
        ]
        if self.exit_nodes:
            exit_nodes_line = ",".join(self.exit_nodes)
            lines.extend([
                f"ExitNodes {exit_nodes_line}",
                "StrictNodes 1",
            ])
        self.config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def start(self, env: Optional[dict[str, str]] = None) -> None:
        if self.process and self.is_running:
            raise TorInstanceError("Tor instance already running")
        self.create_config()
        lock_file = self.data_dir / "lock"
        if lock_file.exists():
            self._logger.info("Removing stale lock file %s", lock_file)
            lock_file.unlink()
        try:
            self.process = subprocess.Popen(
                [self.tor_binary, "-f", str(self.config_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self._logger.info("Starting Tor instance on port %s", self.socks_port)
        except FileNotFoundError as error:  # pragma: no cover - system dependency
            raise TorInstanceError("Tor binary not found") from error

    async def wait_until_ready(self, timeout: Optional[float] = None) -> None:
        effective_timeout = timeout if timeout is not None else self.startup_timeout_seconds
        deadline = time.time() + effective_timeout
        while time.time() < deadline:
            if self.is_running and await self._socks_port_ready():
                self._ensure_pid_file()
                return
            await asyncio.sleep(1)
        exit_code = self.process.poll() if self.process else None
        stderr_output = ""
        stdout_output = ""
        if self.process and exit_code is not None:
            if self.process.stderr:
                try:
                    stderr_output = self.process.stderr.read().decode("utf-8", errors="ignore").strip()
                except Exception:  # noqa: BLE001
                    stderr_output = ""
            if self.process.stdout:
                try:
                    stdout_output = self.process.stdout.read().decode("utf-8", errors="ignore").strip()
                except Exception:  # noqa: BLE001
                    stdout_output = ""
            self.process = None
        self._logger.error(
            "Tor instance on port %s timed out after %.1fs (exit code: %s)",
            self.socks_port,
            effective_timeout,
            exit_code if exit_code is not None else "running",
        )
        combined_output = (stderr_output or "")
        if stdout_output:
            combined_output = f"{combined_output}\n{stdout_output}" if combined_output else stdout_output
        log_hint = f" Inspect {self.log_path} for details." if self.log_path else ""
        message = f"Tor instance did not become ready within {effective_timeout:.1f} seconds.{log_hint}"
        if exit_code is not None:
            message = (
                f"Tor instance exited with code {exit_code}: {combined_output or 'no additional output'}."
                f"{log_hint}"
            )
        raise TorInstanceError(message)

    async def _socks_port_ready(self) -> bool:
        try:
            response = await self._async_tor_get("https://check.torproject.org", 2.0)
            return response.status == 200
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return False

    async def _async_tor_get(self, url: str, timeout_seconds: float) -> aiohttp.ClientResponse:
        connector = ProxyConnector.from_url(f'socks5://127.0.0.1:{self.socks_port}')
        timeout = ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.get(url) as response:
                return response

    @property
    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 15.0) -> None:
        if not self.process:
            return
        if not self.is_running:
            return
        self._logger.info("Stopping Tor instance on port %s", self.socks_port)
        self.process.send_signal(signal.SIGINT)
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._logger.warning("Force killing Tor instance on port %s", self.socks_port)
            self.process.kill()
        finally:
            self.process = None
            self._cleanup_pid_file()
            lock_file = self.data_dir / "lock"
            try:
                if lock_file.exists():
                    lock_file.unlink()
            except Exception:
                pass

    def force_kill(self) -> None:
        if self.process and self.is_running:
            self.process.kill()
            self.process = None
            self._cleanup_pid_file()
            lock_file = self.data_dir / "lock"
            try:
                if lock_file.exists():
                    lock_file.unlink()
            except Exception:
                pass

    def update_exit_nodes(self, exit_nodes: Iterable[str]) -> None:
        self.exit_nodes = list(exit_nodes)
        self.create_config()
        if self.process and self.is_running:
            self.process.send_signal(signal.SIGHUP)
            self._logger.info("Reloaded exit nodes for port %s", self.socks_port)

    def rotate_circuits(self) -> None:
        if not self.is_running:
            raise TorInstanceError("Tor process not running")
        if not self.pid_file.exists():
            self._ensure_pid_file()
        command = [
            self.tor_binary,
            "--signal",
            "NEWNYM",
            "--PidFile",
            str(self.pid_file),
        ]
        try:
            subprocess.run(
                command,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._logger.info("Requested NEWNYM for port %s", self.socks_port)
        except FileNotFoundError as error:  # pragma: no cover - system dependency
            raise TorInstanceError("Tor binary not found") from error
        except subprocess.CalledProcessError as error:
            stderr = (error.stderr or "").strip()
            stdout = (error.stdout or "").strip()
            message = stderr or stdout or str(error)
            raise TorInstanceError(f"Failed to rotate circuits: {message}") from error

    async def perform_health_check(self) -> dict[str, str]:
        if not self.is_running:
            raise TorHealthCheckError("Tor process not running")
        attempts = max(1, self.max_health_retries)
        last_error: Optional[Exception] = None
        for attempt in range(attempts):

            try:
                response = await self._async_tor_get(self.health_check_url, self.health_timeout_seconds)
                response.raise_for_status()
                return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as error:
                last_error = error
                self._logger.warning(
                    "Health check attempt %s/%s failed for port %s: %s",
                    attempt + 1,
                    attempts,
                    self.socks_port,
                    error,
                )
                await asyncio.sleep(2)
        raise TorHealthCheckError("Health check failed") from last_error

    def _ensure_pid_file(self) -> None:
        if self.pid_file.exists():
            return
        if self.process and self.process.pid:
            try:
                self.pid_file.write_text(str(self.process.pid), encoding="utf-8")
            except OSError as error:  # pragma: no cover - filesystem race
                self._logger.warning(
                    "Unable to persist pid file for port %s: %s", self.socks_port, error
                )

    def _cleanup_pid_file(self) -> None:
        try:
            self.pid_file.unlink(missing_ok=True)
        except TypeError:  # pragma: no cover - Python < 3.8 fallback
            try:
                self.pid_file.unlink()
            except FileNotFoundError:
                pass