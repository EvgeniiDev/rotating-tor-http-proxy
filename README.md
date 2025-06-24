# Rotating Tor HTTP Proxy

![Version](https://img.shields.io/badge/version-2.1.0-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Python](https://img.shields.io/badge/python-3.13+-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-blue.svg)

Высокопроизводительная система ротации Tor-прокси с веб-интерфейсом управления, автоматическим балансированием нагрузки и мониторингом в реальном времени. **Использует Privoxy для конвертации SOCKS5 в HTTP и честного round-robin балансирования.**

## 🚀 Ключевые возможности

- **Tor SOCKS5 → Privoxy → HTTP** для оптимальной производительности
- **Автоматическая ротация IP-адресов** через множественные Tor-узлы
- **Веб-панель администрирования** с интуитивным интерфейсом
- **Честная балансировка нагрузки** через HAProxy (HTTP mode)
- **Round-robin распределение** запросов между Tor узлами
- **Мониторинг в реальном времени** состояния всех сервисов
- **Географическое распределение** Tor-узлов по странам/подсетям
- **Docker-контейнеризация** для простого развертывания
- **HTTP прокси** с корректным пониманием границ запросов
- **Автоматическое восстановление** неработающих узлов
- **Единый Privoxy** для всех Tor экземпляров (оптимизация ресурсов)

## 🔄 Новая архитектура: Tor + Privoxy + HAProxy

### Архитектура системы

**Компоненты:**
- **Multiple Tor instances** на SOCKS5 портах (9050, 9051, 9052...)
- **Единый Privoxy** конвертирует все SOCKS5 в HTTP (порт 8118)
- **HAProxy** балансирует HTTP запросы между экземплярами (порт 4444)

**Преимущества новой архитектуры:**
- **Экономия ресурсов**: один Privoxy вместо N экземпляров
- **Упрощенное управление**: единая точка конфигурации HTTP
- **Лучшая производительность**: Privoxy оптимизирован для конвертации
- **Честный round-robin**: HAProxy в HTTP режиме

### Как это работает

```
HTTP Client → HAProxy (HTTP mode) → Privoxy → Multiple Tor SOCKS → Internet
     ↓              ↓                   ↓           ↓
  Request 1    →  Load Balance    →  Convert   →  Tor 9050  →  Exit IP 1
  Request 2    →  Load Balance    →  Convert   →  Tor 9051  →  Exit IP 2  
  Request 3    →  Load Balance    →  Convert   →  Tor 9052  →  Exit IP 3
  Request 4    →  Load Balance    →  Convert   →  Tor 9050  →  Exit IP 1
```

## 🏗️ Архитектура системы

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Client Apps   │───▶│    HAProxy       │───▶│   Tor Network   │
│                 │    │  Load Balancer   │    │   (Multiple     │
└─────────────────┘    │  (Port 8080)     │    │   HTTP Tunnels) │
                       └──────────────────┘    └─────────────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │   Admin Panel    │
                       │   (Port 5000)    │
                       │  + SocketIO      │
                       └──────────────────┘
                                │
                                ▼
                       ┌──────────────────┐
                       │ Tor Network Mgr  │
                       │ + Config Mgr     │
                       │ + HAProxy Mgr    │
                       └──────────────────┘
```

### Компоненты системы

#### 🎛️ **Admin Panel** (`admin_panel.py`)
- **Flask** веб-сервер с **SocketIO** для реального времени
- Управление Tor-узлами (создание, удаление, мониторинг)
- Визуализация статистики и производительности
- RESTful API для программного управления

#### 🌐 **Tor Network Manager** (`tor_network_manager.py`)
- Управление жизненным циклом Tor-процессов
- Автоматическое создание конфигураций для разных стран
- Мониторинг здоровья узлов и автовосстановление
- Интеграция с HAProxy для балансировки

#### ⚖️ **HAProxy Manager** (`haproxy_manager.py`)
- Динамическое управление конфигурацией HAProxy в **HTTP режиме**
- **Round-robin балансировка** HTTP запросов между Tor HTTP туннелями
- **Проверка работоспособности** HTTP туннелей Tor
- Автоматическое исключение неработающих узлов
- Статистика и метрики производительности
- Мониторинг состояния HTTP туннелей

#### ⚙️ **Config Manager** (`config_manager.py`)
- Централизованное управление конфигурациями
- Генерация конфигураций Tor с **HTTPTunnelPort**
- Шаблоны для Tor HTTP туннелей и HAProxy
- Валидация и применение настроек

## 📁 Структура проекта

```
rotating-tor-http-proxy/
├── 🐳 docker-compose.yml          # Docker Compose конфигурация
├── 🐳 Dockerfile                  # Образ контейнера
├── 📜 README.md                   # Документация проекта
├── 🔧 run.bat                     # Скрипт запуска для Windows
├── 🔧 install_ubuntu.sh           # Установка на Ubuntu
├── 🐍 steamProxyCheker.py         # Утилита проверки SOCKS5 прокси (legacy)
├── 🐍 httpTunnelProxyChecker.py   # Утилита проверки HTTP туннелей
├── 🐍 test_http_tunnel.py         # Тестирование отдельных HTTP туннелей
└── src/                           # Исходный код
    ├── 🌐 admin_panel.py           # Веб-интерфейс управления
    ├── ⚙️ config_manager.py        # Управление конфигурациями
    ├── ⚖️ haproxy_manager.py       # Менеджер HAProxy
    ├── 🔧 haproxy.cfg              # Шаблон конфигурации HAProxy
    ├── 📊 models.py                # Модели данных
    ├── 📦 requirements.txt         # Python зависимости
    ├── 🚀 start_with_admin.sh      # Скрипт запуска
    ├── 🌐 tor_network_manager.py   # Менеджер Tor-сети
    └── 📱 templates/               # HTML шаблоны
        └── admin.html              # Интерфейс админ-панели
```

## 🚀 Быстрый старт

### Вариант 1: Docker (Рекомендуется)

```bash
# Клонирование репозитория
git clone <repository-url>
cd rotating-tor-http-proxy

# Запуск с помощью Docker Compose
docker-compose up -d

# Проверка статуса
docker-compose ps
```

### Вариант 2: Нативная установка на Ubuntu

```bash
# Запуск скрипта установки
chmod +x install_ubuntu.sh
./install_ubuntu.sh

# Переход в директорию с исходным кодом
cd src

# Установка Python зависимостей
pip3 install -r requirements.txt

# Запуск системы
chmod +x start_with_admin.sh
./start_with_admin.sh
```

### Вариант 3: Windows

```bash
# Запуск через PowerShell или CMD
run.bat
```

## 🎮 Использование

### Доступ к интерфейсам

После успешного запуска системы доступны следующие интерфейсы:

| Сервис | URL | Описание |
|--------|-----|----------|
| 🎛️ **Админ-панель** | http://localhost:5000 | Основной интерфейс управления |
| 📊 **HAProxy Stats** | http://localhost:4444 | Статистика балансировщика |
| 🌐 **HTTP Proxy** | http://localhost:8080 | Основной прокси-сервер |

### Веб-интерфейс администрирования

1. **Откройте админ-панель**: http://localhost:5000
2. **Управление подсетями**:
   - Выберите страну из выпадающего списка
   - Укажите количество Tor-узлов для региона
   - Нажмите "Start Subnet" для активации
3. **Мониторинг**:
   - Отслеживайте статус всех узлов в реальном времени
   - Просматривайте статистику трафика и производительности
   - Получайте уведомления о проблемах

### Программное управление через API

```python
import requests

# Получение статуса всех подсетей
response = requests.get('http://localhost:5000/api/subnets')
subnets = response.json()

# Запуск новой подсети (5 узлов в США)
requests.post('http://localhost:5000/api/start_subnet', json={
    'country': 'us',
    'count': 5
})

# Остановка подсети
requests.post('http://localhost:5000/api/stop_subnet', json={
    'country': 'us'
})
```

### Использование прокси в приложениях

#### Python с requests

```python
import requests

proxies = {
    'http': 'http://localhost:8080',
    'https': 'http://localhost:8080'
}

response = requests.get('https://httpbin.org/ip', proxies=proxies)
print(f"Текущий IP: {response.json()['origin']}")
```

#### cURL

```bash
curl --proxy http://localhost:8080 https://httpbin.org/ip
```

#### Браузер (Firefox)

1. Настройки → Сеть → Параметры подключения
2. Ручная настройка прокси
3. SOCKS Host: `localhost`, Port: `1080`
4. SOCKS v5

## 📊 Мониторинг и диагностика

### Логи системы

```bash
# Просмотр логов Docker контейнера
docker-compose logs -f tor-proxy-admin

# Логи отдельных компонентов
docker exec -it tor-proxy-with-admin tail -f /var/log/tor/tor.log
```

### Проверка работоспособности

```bash
# Проверка HTTP прокси
python3 test_http_proxy.py

# Проверка через HAProxy stats
curl http://localhost:4444/stats

# Проверка смены IP
for i in {1..5}; do
    curl --proxy http://localhost:8080 https://httpbin.org/ip
    sleep 2
done
```

### Метрики производительности

- **Время отклика**: отображается в админ-панели
- **Пропускная способность**: статистика HAProxy
- **Успешность соединений**: процент успешных подключений
- **Географическое распределение**: активные страны и узлы


**⚠️ Отказ от ответственности**: Этот проект предназначен только для законного использования. Пользователи несут полную ответственность за соблюдение местного законодательства при использовании Tor и прокси-сервисов.

## 🔧 Подробная конфигурация Tor узлов

### 📋 Архитектура конфигурации Tor

Система автоматически создает и управляет множественными экземплярами Tor, каждый из которых имеет уникальную конфигурацию и работает на отдельных портах. Это обеспечивает высокую производительность и надежность.

#### 🏗️ Структура экземпляров Tor

```
Tor Instance 1:  SOCKS: 10000, Control: 20000, Data: /var/lib/tor/data_1
Tor Instance 2:  SOCKS: 10001, Control: 20001, Data: /var/lib/tor/data_2
Tor Instance N:  SOCKS: 1000N, Control: 2000N, Data: /var/lib/tor/data_N
```

### ⚙️ Автоматическая генерация конфигураций

#### Базовая конфигурация torrc

Каждый Tor-узел создается с индивидуальной конфигурацией:

```bash
# Tor Instance {instance_id}
HTTPTunnelPort 127.0.0.1:{http_port}  # Уникальный HTTP туннель порт
ControlPort 127.0.0.1:{ctrl_port}     # Порт управления
HashedControlPassword 16:872860B76453A77D60CA2BB8C1A7042072093276A3D701AD684053EC4C
PidFile /var/lib/tor/tor_{instance_id}.pid
RunAsDaemon 0                          # Работа в foreground для Docker
DataDirectory /var/lib/tor/data_{instance_id}  # Изолированная директория данных
GeoIPFile /usr/share/tor/geoip
GeoIPv6File /usr/share/tor/geoip6

# Настройки производительности
NewCircuitPeriod 10                    # Новый круг каждые 10 секунд
MaxCircuitDirtiness 60                 # Максимальное время жизни круга
UseEntryGuards 0                       # Отключение guard узлов для ротации
LearnCircuitBuildTimeout 1             # Обучение таймаутам
MaxClientCircuitsPending 16            # Максимум ожидающих соединений

# Настройки безопасности
ExitRelay 0                           # Не быть exit узлом
RefuseUnknownExits 0                  # Разрешить неизвестные exit узлы
ClientOnly 1                          # Только клиентский режим
UseMicrodescriptors 1                 # Использовать микродескрипторы
SafeLogging 1                         # Безопасное логирование

# Логирование
Log notice stdout                     # Логи в stdout для Docker
```

#### 🌍 Географическая фильтрация (опционально)

При создании узлов для конкретных подсетей добавляются дополнительные директивы:

```bash
# Exit nodes in subnet {subnet}.0.0/16
ExitNodes {subnet}.0.0/16            # Использовать только exit узлы из подсети
StrictNodes 1                        # Строго соблюдать ограничения узлов
```


### 🌐 Управление подсетями

#### Автоматический выбор релеев

Система получает актуальную информацию о Tor релеях из официального API:

```python
# Запрос к Onionoo API для получения информации о релеях
url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,exit_probability"

# Извлечение релеев по IP подсетям
for relay in relay_data['relays']:
    ip_address = extract_ip_from_addresses(relay['or_addresses'])
    if ip_address:
        ip_parts = ip_address.split('.')
        if len(ip_parts) >= 2:
            subnet = f"{ip_parts[0]}.{ip_parts[1]}"  # Первые два октета
            relay_info = {
                'ip': ip_address,
                'country': relay.get('country', 'Unknown'),
                'exit_probability': relay.get('exit_probability', 0)
            }
```


### 🔄 Ротация и обновление цепей

#### Автоматическая ротация

```bash
# Конфигурация для частой смены IP
NewCircuitPeriod 10          # Новая цепь каждые 10 секунд
MaxCircuitDirtiness 60       # Принудительное обновление через 60 секунд
UseEntryGuards 0            # Отключение постоянных guard узлов
```

#### Принудительное обновление цепей

Система может принудительно обновлять цепи через Control Port:

```python
def renew_tor_circuit(ctrl_port):
    """Принудительное обновление цепи Tor"""
    try:
        # Подключение к Control Port
        controller = stem.control.Controller.from_port(port=ctrl_port)
        controller.authenticate(password="your_control_password")
        
        # Команда на создание новой цепи
        controller.signal(stem.Signal.NEWNYM)
        
        controller.close()
        return True
    except Exception as e:
        logger.error(f"Failed to renew circuit: {e}")
        return False
```

### 📊 Мониторинг конфигураций

#### Проверка статуса узлов

```python
def check_tor_instance_health(instance_id, http_port):
    """Проверка здоровья Tor узла"""
    try:
        # Тест HTTP соединения
        proxies = {'http': f'http://127.0.0.1:{http_port}', 'https': f'http://127.0.0.1:{http_port}'}
        response = requests.get(
            'https://check.torproject.org/api/ip', 
            proxies=proxies, 
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            return {
                'status': 'healthy',
                'ip': data.get('IP'),
                'is_tor': data.get('IsTor', False)
            }
    except Exception as e:
        return {
            'status': 'unhealthy',
            'error': str(e)
        }
```

#### Автоматическое восстановление

```python
def monitor_and_restart_failed_instances():
    """Мониторинг и перезапуск неработающих узлов"""
    for instance_id, process in tor_processes.items():
        if not (process and process.poll() is None):
            logger.warning(f"Tor instance {instance_id} is down, restarting...")
            
            # Удаление из HAProxy
            haproxy_manager.remove_backend_instance(instance_id)
            
            # Перезапуск узла
            new_process = restart_tor_instance(instance_id)
            
            if new_process:
                tor_processes[instance_id] = new_process
                # Добавление обратно в HAProxy
                ports = get_port_assignment(instance_id)
                haproxy_manager.add_backend_instance(
                    instance_id, 
                    f"127.0.0.1:{ports['socks_port']}"
                )
```

### 🛠️ Кастомизация конфигураций

#### Добавление собственных настроек Tor

Вы можете модифицировать `config_manager.py` для добавления собственных параметров:

```python
def get_custom_tor_config(instance_id, custom_options=None):
    """Создание кастомной конфигурации Tor"""
    base_config = get_tor_config(instance_id, socks_port, ctrl_port)
    
    if custom_options:
        custom_lines = []
        
        # Дополнительные страны для Exit узлов
        if 'exit_countries' in custom_options:
            countries = ','.join(custom_options['exit_countries'])
            custom_lines.append(f"ExitNodes {{{countries}}}")
            custom_lines.append("StrictNodes 1")
        
        # Кастомные настройки производительности
        if 'circuit_timeout' in custom_options:
            custom_lines.append(f"CircuitBuildTimeout {custom_options['circuit_timeout']}")
        
        # Добавление к базовой конфигурации
        base_config += '\n' + '\n'.join(custom_lines)
    
    return base_config
```

#### Настройка для специфических задач

**Для высокой анонимности:**
```bash
# Дополнительные настройки в torrc
EnforceDistinctSubnets 1      # Различные подсети в цепи
FascistFirewall 1             # Строгие правила firewall
LongLivedPorts 21,22,706,1863,5050,5190,5222,5223,6523,6667,6697,8300
```

**Для высокой производительности:**
```bash
# Оптимизация производительности
KeepalivePeriod 60           # Интервал keepalive
NewCircuitPeriod 30          # Менее частая смена цепей
NumEntryGuards 8             # Больше guard узлов
```

**Для специфических стран:**
```bash
# Использование только определенных стран
ExitNodes {us},{ca},{de},{fr},{jp}  # США, Канада, Германия, Франция, Япония
EntryNodes {us},{ca}                # Entry только из США и Канады
StrictNodes 1                       # Строгое соблюдение
```

## 🧪 Тестирование HTTP туннелей

### Инструменты тестирования

Проект включает несколько утилит для тестирования работы HTTP туннелей:

#### 1. **Базовое тестирование** (`test_http_tunnel.py`)
Тестирует отдельные HTTP туннели Tor:

```bash
# Тестирование отдельных портов
python test_http_tunnel.py 10000 10001 10002

# Пример вывода:
# Тестирование HTTP туннеля на порту 10000
# Запрос 1/5: IP 185.220.101.45, время 2.34s
# Запрос 2/5: IP 185.220.102.11, время 1.87s
```

#### 2. **Полное тестирование** (`httpTunnelProxyChecker.py`)
Комплексное тестирование через HAProxy с анализом ротации:

```bash
# Запуск тестирования
python httpTunnelProxyChecker.py

# Анализирует:
# - Процент успешных запросов
# - Время ответа
# - Эффективность ротации IP
# - Распределение по exit узлам
```

#### 3. **Legacy SOCKS5 тестер** (`steamProxyCheker.py`)
Старый тестер для сравнения с SOCKS5 режимом.

### Ключевые метрики тестирования

- **Success Rate**: % успешных HTTP запросов
- **Response Time**: среднее время ответа
- **IP Rotation**: эффективность смены exit IP
- **Round-Robin**: равномерность распределения запросов
- **Connection Health**: состояние HTTP туннелей

### Пример результатов

```
📊 ОБЩАЯ СТАТИСТИКА:
   Всего запросов: 50
   Успешных: 48
   Процент успеха: 96.00%

⏱️  ВРЕМЯ ОТВЕТА:
   Среднее: 2.341s
   Медиана: 2.156s

🔄 РОТАЦИЯ IP:
   Уникальных IP: 12
   Эффективность ротации: 85.2%

📍 РАСПРЕДЕЛЕНИЕ ПО IP:
   185.220.101.45: 8 запросов (16.7%)
   95.142.47.123: 7 запросов (14.6%)
   ...
```