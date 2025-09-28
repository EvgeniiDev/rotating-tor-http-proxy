#!/usr/bin/env python3
"""
Интеграционный модуль для координации TorParallelRunner и HAProxy балансировщика
"""

import logging
import time
import threading
from pathlib import Path
from typing import List, Optional

from tor_parallel_runner import TorParallelRunner
from config_manager import TorConfigBuilder
from haproxy_tor_pool_manager import (
    HAProxyTorPoolManager,
    DEFAULT_FRONTEND_PORT,
    DEFAULT_STATS_PORT,
)
from tor_relay_manager import TorRelayManager

logger = logging.getLogger(__name__)


class TorBalancerManager:
    """Единый менеджер пула Tor + HAProxy."""

    def __init__(self,
                 frontend_port: int = 9999,
                 stats_port: int = 8404,
                 base_tor_port: int = 10000,
                 config_dir: Optional[str] = None,
                 max_workers: int = 10,
                 config_builder: Optional[TorConfigBuilder] = None,
                 runner: Optional[TorParallelRunner] = None,
                 checker=None,
                 http_balancer: Optional[HAProxyTorPoolManager] = None,
                 relay_manager: Optional[TorRelayManager] = None):
        if frontend_port != DEFAULT_FRONTEND_PORT:
            logger.warning(
                "Игнорируем нестандартный frontend_port=%s, используем стандартный %s",
                frontend_port,
                DEFAULT_FRONTEND_PORT,
            )
        if stats_port != DEFAULT_STATS_PORT:
            logger.warning(
                "Игнорируем нестандартный stats_port=%s, используем стандартный %s",
                stats_port,
                DEFAULT_STATS_PORT,
            )

        self.frontend_port = DEFAULT_FRONTEND_PORT
        self.stats_port = DEFAULT_STATS_PORT
        self.base_tor_port = base_tor_port

        # Совместимость со старым API: сохраняем checker и runner, если переданы
        self.config_builder = config_builder or TorConfigBuilder()
        self.checker = checker

        self.tor_runner = runner or TorParallelRunner(
            config_builder=self.config_builder,
            max_workers=max_workers
        )
        self.runner = self.tor_runner

        config_path = Path(config_dir) / "haproxy.cfg" if config_dir else None

        self.haproxy_manager = http_balancer or HAProxyTorPoolManager(
            tor_runner=self.tor_runner,
            config_path=str(config_path) if config_path else None
        )

        self.relay_manager = relay_manager or TorRelayManager()

        self._running = False
        self._lock = threading.RLock()
        self._allocated_ports: List[int] = []
        self._start_time: Optional[float] = None

    def _find_free_ports(self, count: int) -> List[int]:
        """Находит свободные порты для Tor процессов"""
        import socket
        
        free_ports = []
        port = self.base_tor_port
        max_attempts = count * 10
        
        while len(free_ports) < count and (port - self.base_tor_port) < max_attempts:
            if self._is_port_free(port):
                free_ports.append(port)
            port += 1
            
        if len(free_ports) < count:
            raise RuntimeError(f"Не удалось найти {count} свободных портов")
            
        logger.info(f"Найдено {len(free_ports)} свободных портов: {free_ports}")
        return free_ports

    def _is_port_free(self, port: int) -> bool:
        """Проверяет, свободен ли порт"""
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(('127.0.0.1', port))
                return True
        except OSError:
            return False

    def _resolve_exit_nodes(self, exit_nodes: List[str], tor_count: int) -> List[str]:
        if exit_nodes:
            return exit_nodes

        logger.info("🔎 Запрашиваем exit-ноды через TorRelayManager")

        try:
            relay_data = self.relay_manager.fetch_tor_relays()
        except Exception as exc:
            logger.error(f"Не удалось получить данные о Tor релеях: {exc}")
            return []

        if not relay_data:
            logger.error("TorRelayManager вернул пустой ответ при получении релеев")
            return []

        relay_entries = self.relay_manager.extract_relay_ips(relay_data)
        if not relay_entries:
            logger.error("TorRelayManager не смог извлечь ни одной exit-ноды")
            return []

        target_total = max(tor_count, tor_count * 6)
        unique_ips: List[str] = []
        seen = set()
        for entry in relay_entries:
            ip = entry.get('ip')
            if not ip or ip in seen:
                continue
            seen.add(ip)
            unique_ips.append(ip)
            if len(unique_ips) >= target_total:
                break

        if len(unique_ips) < tor_count:
            logger.warning(
                f"Недостаточно exit-нод: {len(unique_ips)} найдено, требуется минимум {tor_count}"
            )
        else:
            logger.info(f"🌍 Используем {len(unique_ips)} exit-нод, полученных из TorRelayManager")

        return unique_ips

    def _distribute_exit_nodes(self, exit_nodes: List[str], tor_count: int) -> List[List[str]]:
        """Распределяет exit nodes между Tor процессами"""
        if not exit_nodes:
            return [[] for _ in range(tor_count)]
            
        nodes_per_tor = max(1, len(exit_nodes) // tor_count)
        distributed = []
        
        for i in range(tor_count):
            start_idx = i * nodes_per_tor
            end_idx = min(start_idx + nodes_per_tor, len(exit_nodes))
            distributed.append(exit_nodes[start_idx:end_idx])
            
        return distributed

    def start_pool(self, tor_count: int, exit_nodes: List[str] = None) -> bool:
        """Запускает полный пул: Tor процессы + HAProxy балансировщик"""
        with self._lock:
            if self._running:
                logger.warning("Пул уже запущен")
                return True
                
            try:
                logger.info(f"🚀 Запуск интегрированного пула: {tor_count} Tor процессов + HAProxy")
                
                # Проверяем зависимости
                deps_ok, missing = self.haproxy_manager.check_dependencies()
                if not deps_ok:
                    logger.error(f"❌ Отсутствуют зависимости: {', '.join(missing)}")
                    return False
                
                # Находим свободные порты
                ports = self._find_free_ports(tor_count)
                self._allocated_ports = ports
                
                resolved_exit_nodes = self._resolve_exit_nodes(exit_nodes or [], tor_count)

                # Распределяем exit nodes
                exit_nodes_per_tor = self._distribute_exit_nodes(resolved_exit_nodes, tor_count)
                
                # Запускаем Tor процессы (без ожидания health check)
                logger.info(f"⏳ Запуск {tor_count} Tor процессов без ожидания health-check")
                started_ports = self.tor_runner.start_many(ports, exit_nodes_per_tor)
                if len(started_ports) < tor_count:
                    logger.warning(
                        f"Запуск Tor завершился не полностью: {len(started_ports)}/{tor_count} процессов сообщили об успехе"
                    )

                # Применяем конфигурацию HAProxy и перезагружаем службу
                logger.info("⚙️ Применение конфигурации HAProxy через systemd reload")
                if not self.haproxy_manager.apply_config(started_ports):
                    logger.error("❌ Не удалось обновить конфигурацию HAProxy")
                    self.tor_runner.stop_all()
                    return False
                
                self._running = True
                self._start_time = time.time()
                self._allocated_ports = started_ports

                logger.info("🎉 Интегрированный пул успешно запущен!")
                return True
                
            except Exception as e:
                logger.error(f"❌ Ошибка запуска пула: {e}")
                self.stop_pool()
                return False

    def stop_pool(self):
        """Останавливает весь пул"""
        with self._lock:
            if not self._running:
                return
                
            logger.info("🛑 Остановка интегрированного пула...")
            # Останавливаем Tor процессы
            self.tor_runner.stop_all()

            # Обновляем конфигурацию HAProxy, чтобы очистить бэкенд
            try:
                if not self.haproxy_manager.apply_config([]):
                    logger.warning("Не удалось обновить конфигурацию HAProxy при остановке")
            except Exception as exc:
                logger.error("Не удалось обновить конфигурацию HAProxy при остановке: %s", exc)
            
            self._running = False
            self._allocated_ports = []
            self._start_time = None
            logger.info("✅ Интегрированный пул остановлен")

    def get_stats(self):
        """Возвращает объединённую статистику"""
        with self._lock:
            haproxy_stats = self.haproxy_manager.get_stats()
            tor_statuses = self.tor_runner.get_statuses()
            uptime = 0.0
            if self._start_time:
                uptime = max(0.0, time.time() - self._start_time)
            
            return {
                'pool_running': self._running,
                'haproxy_running': haproxy_stats['haproxy_running'],
                'tor_processes_total': len(tor_statuses),
                'tor_processes_running': haproxy_stats['tor_processes_running'],
                'tor_ports': haproxy_stats['tor_ports'],
                'frontend_port': haproxy_stats['frontend_port'],
                'stats_port': haproxy_stats['stats_port'],
                'config_dir': haproxy_stats['config_dir'],
                'uptime': uptime
            }

    def is_running(self) -> bool:
        """Проверяет, запущен ли пул"""
        return self._running

    @property
    def pid_file(self):
        """Возвращает путь к PID-файлу HAProxy."""
        return self.haproxy_manager.pid_file

    def __del__(self):
        """Cleanup при удалении"""
        try:
            if self._running:
                self.stop_pool()
        except:
            pass


# Совместимость с прежним именем
TorHAProxyIntegrator = TorBalancerManager