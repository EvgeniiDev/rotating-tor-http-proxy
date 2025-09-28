#!/usr/bin/env python3
"""
Основной скрипт запуска HAProxy Tor Pool Manager
Полная Python реализация без shell скриптов
"""

import os
import sys
import signal
import logging
import argparse
import time

# Добавляем текущую директорию в Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tor_haproxy_integrator import TorHAProxyIntegrator


# Настройка логирования
def setup_logging(log_level: str = 'INFO'):
    """Настраивает логирование"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )


# Глобальная переменная для менеджера
pool_manager: TorHAProxyIntegrator | None = None


def signal_handler(signum, frame):
    """Обработчик сигналов для graceful shutdown"""
    global pool_manager

    signal_names = {
        signal.SIGTERM: 'SIGTERM',
        signal.SIGINT: 'SIGINT',
        signal.SIGQUIT: 'SIGQUIT'
    }

    signal_name = signal_names.get(signum, f'SIG{signum}')
    logging.info(f"📨 Получен сигнал {signal_name}, начинаю graceful shutdown...")

    if pool_manager:
        try:
            pool_manager.stop_pool()
            logging.info("✅ HAProxy Tor пул остановлен успешно")
        except Exception as exc:  # noqa: BLE001
            logging.error("❌ Ошибка при остановке пула: %s", exc)

    sys.exit(0)


def main():
    """Основная функция"""
    global pool_manager

    parser = argparse.ArgumentParser(description='HAProxy Tor Pool Manager')
    parser.add_argument('--tor-count', type=int, default=5,
                        help='Количество Tor процессов (по умолчанию: 5)')
    args = parser.parse_args()

    setup_logging('INFO')

    tor_count = args.tor_count
    tor_env = os.getenv('TOR_PROCESSES')
    if tor_env is not None:
        try:
            env_value = int(tor_env)
            if env_value > 0:
                tor_count = env_value
                logging.info(
                    "TOR_PROCESSES=%s переопределяет параметр --tor-count", env_value
                )
            else:
                logging.warning("TOR_PROCESSES должно быть положительным, игнорируем %s", tor_env)
        except ValueError:
            logging.warning(
                "TOR_PROCESSES=%s не является числом, используем значение %s", tor_env, tor_count
            )

    logging.info("🚀 HAProxy Tor Pool Manager - запуск")
    logging.info("📊 Параметры: tor_processes=%s", tor_count)

    # Настраиваем обработчики сигналов
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGQUIT, signal_handler)

    try:
        pool_manager = TorHAProxyIntegrator(max_workers=tor_count)

        if not pool_manager.start_pool(tor_count):
            logging.error("❌ Не удалось запустить пул")
            sys.exit(1)

        stats = pool_manager.get_stats()
        logging.info("=" * 60)
        logging.info("🎉 HAProxy Tor Pool Manager успешно запущен!")
        logging.info("🌐 Frontend SOCKS5 proxy: 127.0.0.1:%s", stats['frontend_port'])
        logging.info("📊 Статистика HAProxy: http://127.0.0.1:%s/stats", stats['stats_port'])
        logging.info("🔄 Активных Tor процессов: %s", stats['tor_processes_running'])
        logging.info("🚪 Используемые порты: %s", stats['tor_ports'])
        logging.info("📁 Конфигурационная директория: %s", stats['config_dir'])
        logging.info("=" * 60)

        try:
            while pool_manager.is_running():
                time.sleep(60)
                current_stats = pool_manager.get_stats()
                logging.debug(
                    "📊 Статус: HAProxy=%s, Tor=%s/%s",
                    current_stats['haproxy_running'],
                    current_stats['tor_processes_running'],
                    current_stats['tor_processes_total'],
                )
        except KeyboardInterrupt:
            logging.info("📨 Получен Ctrl+C, завершение работы...")

    except Exception as exc:  # noqa: BLE001
        logging.error("❌ Критическая ошибка: %s", exc)
        import traceback
        logging.error(traceback.format_exc())
        sys.exit(1)

    finally:
        if pool_manager:
            try:
                pool_manager.stop_pool()
            except Exception as exc:  # noqa: BLE001
                logging.error("❌ Ошибка при финальной остановке: %s", exc)

        logging.info("✅ HAProxy Tor Pool Manager завершён")


if __name__ == "__main__":
    main()
