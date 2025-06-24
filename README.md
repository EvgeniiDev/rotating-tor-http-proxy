# Tor HTTP Proxy

HTTP прокси-сервер с автоматической балансировкой нагрузки через множественные Tor инстансы.

Нативная реализация на Python работает эффективнее Docker-решений при тех же ресурсах.

## Компоненты

- **HTTP Load Balancer** - самописный балансировщик с round-robin алгоритмом
- **Tor Network Manager** - управление Tor инстансами по подсетям
- **Admin Panel** - веб-интерфейс для мониторинга и управления
- **Statistics Manager** - сбор метрик производительности

## Архитектура

```
HTTP Client → HTTP Load Balancer:8080 → SOCKS5 Tor Instances:10000+ → Internet
                     ↓
              Admin Panel:5000
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

### Админ панель
Откройте `http://localhost:5000`

### Управление через systemd
```bash
sudo systemctl start tor-http-proxy
sudo systemctl stop tor-http-proxy
sudo systemctl status tor-http-proxy
```

## API

### Статус системы
```bash
curl http://localhost:5000/api/status
```

### Управление подсетями
```bash
curl -X POST http://localhost:5000/api/subnet/1.2/start -d '{"instances": 3}'
curl -X POST http://localhost:5000/api/subnet/1.2/stop
```

### Статистика балансировщика
```bash
curl http://localhost:5000/api/balancer/stats
curl http://localhost:5000/api/balancer/top-proxies
```

## Порты

- **8080** - HTTP Load Balancer
- **5000** - Admin Panel  
- **10000+** - SOCKS5 Tor instances

## Логи

```bash
journalctl -u tor-http-proxy -f
```

## Системные требования

- **200 Tor инстансов**: 2 CPU, 4GB RAM, 8GB swap
- **Поддержка**: Ubuntu 22.04 LTS
- **Преимущество**: работает без Docker контейнеризации
`   