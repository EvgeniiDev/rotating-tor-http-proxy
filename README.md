# Tor HTTP Proxy

HTTP прокси-сервер с автоматической балансировкой нагрузки через множественные Tor инстансы.

Нативная реализация на Python работает эффективнее Docker-решений при тех же ресурсах.

## Компоненты

- **HTTP Load Balancer** - самописный балансировщик с round-robin алгоритмом
- **Tor Network Manager** - управление Tor инстансами по подсетям
- **Statistics Manager** - сбор метрик производительности

## Архитектура

```
HTTP Client → HTTP Load Balancer:8080 → SOCKS5 Tor Instances:10000+ → Internet
```

## Установка

```bash
git clone <repository>
cd rotating-tor-http-proxy
sudo ./install_ubuntu.sh
```

## Использование

### HTTP прокси
```bash
curl --proxy http://localhost:8080 https://httpbin.org/ip
```

### Параллельный запуск Tor процессов
Система поддерживает параллельный запуск Tor процессов для ускорения инициализации:

```bash
# Запуск с 50 процессами, максимум 10 параллельно
TOR_PROCESSES=50 python src/main.py

# Или через переменную окружения
export TOR_PROCESSES=50
python src/main.py
```

### Тестирование параллельного запуска
```bash
python test_parallel_startup.py
```

### Управление через systemd
```bash
sudo systemctl start tor-http-proxy
sudo systemctl stop tor-http-proxy
sudo systemctl status tor-http-proxy
```

## Порты

- **8080** - HTTP Load Balancer
- **10000+** - SOCKS5 Tor instances

## Производительность

### Параллельный запуск
- **Последовательный запуск**: ~2-3 секунды на процесс
- **Параллельный запуск (10 процессов)**: ~10-15 секунд для 50 процессов
- **Ускорение**: в 3-5 раз быстрее

### Ограничения
- Максимум 10 одновременно запускаемых Tor процессов
- Автоматическое перераспределение узлов при сбоях
- Мониторинг здоровья процессов

## Логи

```bash
journalctl -u tor-http-proxy -f
```

## Очистка временных файлов

Система автоматически очищает временные файлы при запуске, но для ручной очистки можно использовать:

```bash
# Bash скрипт для очистки
./cleanup_temp_files.sh

# Python скрипт для детальной очистки
python src/cleanup_temp_files.py
```
