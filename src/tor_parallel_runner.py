from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional

from .config_manager import TorProxySettings
from .exceptions import TorInstanceError
from .logging_utils import get_logger
from .tor_process import TorInstance, TorRuntimeMetadata
from .utils import generate_port_allocations


@dataclass(frozen=True)
class InstanceStatus:
    instance_id: int
    socks_port: int
    pid_file: str
    running: bool
    last_health_timestamp: Optional[float]
    last_error: Optional[str]


class TorParallelRunner:
    """Manage lifecycle of multiple Tor instances in parallel."""

    def __init__(self, settings: TorProxySettings, tor_binary: str = "tor") -> None:
        self._settings = settings
        self._tor_binary = tor_binary
        self._logger = get_logger("runner")
        self._instances: Dict[int, TorInstance] = {}
        self._last_health: Dict[int, float] = {}
        self._last_error: Dict[int, str] = {}
        self._lock = threading.RLock()

    def _build_instance(self, allocation, exit_nodes: Iterable[str]) -> TorInstance:
        instance_dir = self._settings.tor_data_dir / f"instance_{allocation.instance_id:03d}"
        metadata = TorRuntimeMetadata(
            socks_port=allocation.socks_port,
            config_path=instance_dir / "torrc",
            data_dir=instance_dir / "data",
            log_path=instance_dir / "tor.log",
            pid_file=instance_dir / "tor.pid",
        )
        return TorInstance(
            instance_id=allocation.instance_id,
            tor_binary=self._tor_binary,
            metadata=metadata,
            exit_nodes=list(exit_nodes),
            health_check_url=self._settings.health_check_url,
            health_timeout_seconds=self._settings.health_timeout_seconds,
            max_health_retries=self._settings.health_retries,
            startup_timeout_seconds=self._settings.tor_start_timeout_seconds,
        )

    async def start_many(self, exit_node_map: Mapping[int, Iterable[str]]) -> List[TorInstance]:
        allocations = generate_port_allocations(
            self._settings.tor_base_port,
            self._settings.tor_instances,
            self._settings.tor_max_port,
        )
        created_instances: List[TorInstance] = []
        
        # Use semaphore to limit concurrent startups
        semaphore = asyncio.Semaphore(self._settings.tor_start_batch)
        
        async def limited_start_single(alloc, exit_nodes):
            async with semaphore:
                return await self._start_single(alloc, exit_nodes)
        
        # Process all allocations with concurrency limit
        tasks = [
            limited_start_single(alloc, exit_node_map.get(alloc.instance_id, ()))
            for alloc in allocations
        ]
        
        for task in asyncio.as_completed(tasks):
            try:
                instance = await task
                created_instances.append(instance)
            except TorInstanceError:
                # Error already logged in _start_single
                continue
        return created_instances

    async def _start_single(self, allocation, exit_nodes: Iterable[str]) -> TorInstance:
        instance = self._build_instance(allocation, exit_nodes)
        await self._start_instance_with_retries(instance)
        with self._lock:
            self._instances[allocation.instance_id] = instance
        return instance

    async def _start_instance_with_retries(self, instance) -> None:
        """Common logic for starting a Tor instance with retry handling."""
        attempts = max(1, self._settings.tor_start_retries + 1)

        for attempt in range(1, attempts + 1):
            try:
                instance.start()
                await instance.wait_until_ready(
                    timeout=self._settings.tor_start_timeout_seconds
                )
                # Clear any previous error on successful start
                self._last_error.pop(instance.instance_id, None)
                return
            except TorInstanceError as error:
                self._last_error[instance.instance_id] = str(error)
                instance.force_kill()
                if attempt >= attempts:
                    self._logger.error(
                        "Instance %s failed to start after %s attempts: %s",
                        instance.instance_id,
                        attempts,
                        error,
                    )
                    raise
                self._logger.warning(
                    "Instance %s start attempt %s/%s failed: %s",
                    instance.instance_id,
                    attempt,
                    attempts,
                    error,
                )
                await asyncio.sleep(self._settings.tor_start_retry_delay_seconds)

    def stop_all(self) -> None:
        with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
        for instance in instances:
            try:
                instance.stop()
            except TorInstanceError as error:
                self._logger.error("Failed to stop instance %s: %s", instance.instance_id, error)

    def get_statuses(self) -> List[InstanceStatus]:
        with self._lock:
            statuses = [
                InstanceStatus(
                    instance_id=instance.instance_id,
                    socks_port=instance.socks_port,
                    pid_file=str(instance.pid_file),
                    running=instance.is_running,
                    last_health_timestamp=self._last_health.get(instance.instance_id),
                    last_error=self._last_error.get(instance.instance_id),
                )
                for instance in self._instances.values()
            ]
        return statuses

    async def perform_health_checks(self) -> None:
        with self._lock:
            instances = list(self._instances.values())
        for instance in instances:
            try:
                await instance.perform_health_check()
                self._last_health[instance.instance_id] = time.time()
            except Exception as error:  # noqa: BLE001
                self._last_error[instance.instance_id] = str(error)
                self._logger.warning(
                    "Health check failed for instance %s: %s", instance.instance_id, error
                )

    async def restart_failed_instances(self) -> None:
        with self._lock:
            instances = list(self._instances.items())
        for instance_id, instance in instances:
            if instance.is_running:
                continue
            self._logger.warning("Restarting instance %s", instance_id)
            try:
                await self._start_instance_with_retries(instance)
            except TorInstanceError as error:
                self._last_error[instance_id] = str(error)
                self._logger.error("Failed to restart instance %s: %s", instance_id, error)

    def rotate_all_circuits(self) -> None:
        with self._lock:
            instances = list(self._instances.values())
        for instance in instances:
            if not instance.is_running:
                continue
            try:
                instance.rotate_circuits()
            except TorInstanceError as error:
                self._last_error[instance.instance_id] = str(error)
                self._logger.warning(
                    "Circuit rotation failed for instance %s: %s",
                    instance.instance_id,
                    error,
                )

    def iter_instances(self) -> Iterable[TorInstance]:
        with self._lock:
            return list(self._instances.values())

    def remove_instance(self, instance_id: int) -> None:
        with self._lock:
            instance = self._instances.pop(instance_id, None)
        if not instance:
            return
        instance.stop()
        self._last_health.pop(instance_id, None)
        self._last_error.pop(instance_id, None)
