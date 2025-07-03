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

### Управление через systemd
```bash
sudo systemctl start tor-http-proxy
sudo systemctl stop tor-http-proxy
sudo systemctl status tor-http-proxy
```

## Порты

- **8080** - HTTP Load Balancer
- **10000+** - SOCKS5 Tor instances

## Логи

```bash
journalctl -u tor-http-proxy -f
```
