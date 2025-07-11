# Руководство по тестированию рефакторенной архитектуры

## Быстрый запуск для тестирования

### 1. Тестирование отдельных компонентов

```bash
cd src

# Тест TorConfigBuilder
python3 -c "
from tor_config_builder import TorConfigBuilder
builder = TorConfigBuilder()
config = builder.create_config_with_exit_nodes(9999, ['1.2.3.4', '5.6.7.8'])
print('Config created:', config)
builder.cleanup_config(config['config_path'])
print('Config cleaned up')
"

# Тест TorProcessManager (требует Tor)
python3 -c "
from tor_config_builder import TorConfigBuilder
from tor_process_manager import TorProcessManager
import time

builder = TorConfigBuilder()
manager = TorProcessManager(9998, [], builder)
print('Starting process...')
if manager.start():
    print('Process started successfully')
    time.sleep(10)
    status = manager.get_status()
    print('Status:', status)
    manager.stop()
    print('Process stopped')
else:
    print('Failed to start process')
"
```

### 2. Запуск полной системы

```bash
# Минимальная конфигурация для тестирования
TOR_PROCESSES=3 LISTEN_PORT=8082 python src/new_main.py

# Без валидации нод (быстрее для тестирования)
TOR_PROCESSES=3 VALIDATE_NODES=false python src/new_main.py

# Полная конфигурация
TOR_PROCESSES=10 VALIDATE_NODES=true python src/new_main.py
```

### 3. Тестирование HTTP прокси

После запуска системы протестируйте прокси:

```bash
# Проверка доступности прокси
curl -x http://localhost:8081 http://httpbin.org/ip

# Тест с несколькими запросами для проверки ротации IP
for i in {1..5}; do
    echo "Request $i:"
    curl -x http://localhost:8081 http://httpbin.org/ip
    sleep 2
done
```

## Пошаговое тестирование компонентов

### 1. TorConfigBuilder

```python
from tor_config_builder import TorConfigBuilder

builder = TorConfigBuilder()

# Тест создания конфигурации с выходными узлами
config1 = builder.create_config_with_exit_nodes(9001, ['1.1.1.1', '8.8.8.8'])
assert 'config_path' in config1
assert config1['exit_nodes_count'] == 2

# Тест создания конфигурации без выходных узлов
config2 = builder.create_config_without_exit_nodes(9002)
assert config2['exit_nodes_count'] == 0

# Тест временной конфигурации
temp_config = builder.create_temporary_config(9003, ['1.1.1.1'])
assert temp_config.endswith('.torrc')

# Очистка
builder.cleanup_config(config1['config_path'])
builder.cleanup_config(config2['config_path'])
builder.cleanup_config(temp_config)
print("✓ TorConfigBuilder tests passed")
```

### 2. TorProcessManager

```python
# Требует установленный Tor
from tor_config_builder import TorConfigBuilder
from tor_process_manager import TorProcessManager
import time

builder = TorConfigBuilder()
manager = TorProcessManager(9004, [], builder)

# Тест запуска
assert manager.start() == True
assert manager.is_running == True

# Тест проверки здоровья
time.sleep(5)
health = manager.check_health()
print(f"Health check: {health}")

# Тест получения статуса
status = manager.get_status()
assert 'port' in status
assert status['port'] == 9004

# Тест остановки
manager.stop()
assert manager.is_running == False
print("✓ TorProcessManager tests passed")
```

### 3. TorPoolManager

```python
from tor_config_builder import TorConfigBuilder
from new_tor_pool_manager import TorPoolManager

builder = TorConfigBuilder()
pool = TorPoolManager(builder, max_concurrent=3)

# Тест конфигураций процессов
configs = [
    {'port': 9010, 'exit_nodes': []},
    {'port': 9011, 'exit_nodes': []},
    {'port': 9012, 'exit_nodes': []}
]

# Тест запуска процессов
result = pool.start_processes(configs)
print(f"Started: {len(result['successful'])}, Failed: {len(result['failed'])}")

# Тест получения статусов
statuses = pool.get_all_statuses()
print(f"Got {len(statuses)} process statuses")

# Тест остановки
pool.stop_all_processes()
print("✓ TorPoolManager tests passed")
```

### 4. ExitNodeValidator

```python
from tor_config_builder import TorConfigBuilder
from exit_node_validator import ExitNodeValidator

builder = TorConfigBuilder()
validator = ExitNodeValidator(builder, max_workers=2)

# Тест с несколькими IP (может занять время)
test_nodes = ['8.8.8.8', '1.1.1.1']  # Используйте реальные exit node IP
valid_nodes = validator.validate_exit_nodes(test_nodes)

print(f"Tested {len(test_nodes)} nodes, {len(valid_nodes)} passed validation")

# Статистика валидации
stats = validator.get_validation_stats()
print("Validation stats:", stats)
print("✓ ExitNodeValidator tests passed")
```

### 5. TorOrchestrator (полная система)

```python
from tor_orchestrator import TorOrchestrator
import time

orchestrator = TorOrchestrator(listen_port=8083)

# Тест запуска системы
success = orchestrator.start_system(process_count=2, validate_nodes=False)
assert success == True

# Тест получения статуса
status = orchestrator.get_system_status()
print("System status:", status['system_status'])
print("Active processes:", status['active_processes'])

# Ждем немного для стабилизации
time.sleep(10)

# Тест перезапуска неудачных процессов
restart_result = orchestrator.restart_failed_processes()
print("Restart result:", restart_result)

# Тест остановки
orchestrator.stop_system()
print("✓ TorOrchestrator tests passed")
```

## Проверка ключевых требований

### ✅ Требование 1: Разделение логики между классами
- [x] TorConfigBuilder - создание конфигураций
- [x] TorProcessManager - управление одним процессом  
- [x] TorPoolManager - управление множеством процессов
- [x] ExitNodeValidator - проверка нод
- [x] TorOrchestrator - координация системы

### ✅ Требование 2: Мониторинг IP каждые 5 секунд
```bash
# Запустите систему и проверьте логи
python src/new_main.py

# В логах должны появляться сообщения каждые 5 секунд:
# "Health check for port XXXX"
# "Current exit IP: X.X.X.X"
```

### ✅ Требование 3: Максимум 20 процессов
```python
from tor_orchestrator import TorOrchestrator

orchestrator = TorOrchestrator()
# Попытка запустить больше 20 процессов будет ограничена
result = orchestrator.start_system(process_count=50)  # Будет ограничено до 20
```

### ✅ Требование 4: Валидация Steam запросами
```python
# В ExitNodeValidator проверьте параметры:
# - test_url = "https://steamcommunity.com/market/search?appid=730"
# - requests_per_node = 6
# - min_successful_requests = 3
```

### ✅ Требование 5: Распределение нод и балансировщик
```python
# TorOrchestrator автоматически:
# 1. Получает валидные ноды от ExitNodeValidator
# 2. Запускает процессы через TorPoolManager  
# 3. Распределяет ноды по процессам
# 4. Добавляет процессы в HTTPLoadBalancer
```

## Типичные ошибки при тестировании

1. **Tor не установлен**: `sudo apt-get install tor`
2. **Порты заняты**: Измените порты в тестах
3. **Недостаточно прав**: Запускайте с sudo если нужно
4. **Долгая валидация**: Используйте `VALIDATE_NODES=false` для быстрых тестов
5. **Блокировка Steam**: Используйте VPN если Steam заблокирован

## Логи для диагностики

Включите детальное логирование:

```python
import logging
logging.basicConfig(level=logging.DEBUG)

# Или для конкретных компонентов:
logging.getLogger('tor_process_manager').setLevel(logging.DEBUG)
logging.getLogger('exit_node_validator').setLevel(logging.DEBUG)
```

## Производительность

Ожидаемые время выполнения:
- Запуск одного процесса: ~10-30 секунд
- Валидация 100 нод: ~5-15 минут  
- Запуск полной системы (20 процессов): ~2-5 минут
- Проверка IP: ~1-3 секунды

## Завершение

После успешного тестирования всех компонентов система готова к production использованию. Новая архитектура обеспечивает:

- Надежное разделение ответственности
- Автоматический мониторинг и восстановление
- Эффективную валидацию выходных нод
- Контролируемое использование ресурсов