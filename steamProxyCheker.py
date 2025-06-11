import asyncio
import aiohttp
import time
import statistics
import platform
import json
import traceback
from typing import List, Dict, Any, Counter as TypeCounter
from collections import Counter
from aiohttp_socks import ProxyConnector

class RotatedProxyTester:
    def __init__(self, target_url: str = "https://steamcommunity.com/market/search?appid=730"):
        self.target_url = target_url
        self.results: List[Dict[str, Any]] = []
        
        # Единый SOCKS5 прокси с ротацией
        self.proxy_config = {
            'host': '192.168.1.204',
            'port': 1080,
            'type': 'socks5',
            'id': '192.168.1.204:1080'
        }
        
        # Блокировки для обеспечения потокобезопасности
        self.stats_lock = asyncio.Lock()
        
        # Счетчики статистики
        self.total_requests = 0
        self.successful_requests = 0
        self.rate_limited_429 = 0
        self.connection_errors = 0
        self.timeout_errors = 0
        self.other_errors = 0
        
        # Данные для расчета RPM
        self.request_timestamps = []
        self.success_timestamps = []
        
        # Временные метки для детальной статистики
        self.response_times = []
        self.status_codes = {}
        
        # Реалистичные заголовки для имитации обычного браузера
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'Cache-Control': 'max-age=0'
        }
        
        # Enhanced error tracking
        self.error_details_counter = Counter()
        self.http_error_details = {}
        self.error_timestamps = []
        self.error_samples = {}  # Store sample errors by type
        self.max_error_samples = 5  # Max number of detailed samples per error type
    
    def get_proxy_url(self) -> str:
        """Возвращает URL прокси для подключения"""
        return f"socks5://{self.proxy_config['host']}:{self.proxy_config['port']}"
    
    def _classify_error(self, status_code: int) -> str:
        """Классифицирует тип ошибки по статус коду"""
        if status_code == 200:
            return 'success'
        elif status_code == 429:
            return 'rate_limited'
        elif status_code == 403:
            return 'forbidden'
        elif status_code == 404:
            return 'not_found'
        elif status_code == 500:
            return 'server_error'
        elif status_code == 502:
            return 'bad_gateway'
        elif status_code == 503:
            return 'service_unavailable'
        elif status_code == 504:
            return 'gateway_timeout'
        elif 400 <= status_code < 500:
            return f'client_error_{status_code}'
        elif 500 <= status_code < 600:
            return f'server_error_{status_code}'
        else:
            return f'other_{status_code}'
    
    async def send_request(self, session: aiohttp.ClientSession, request_id: int) -> Dict[str, Any]:
        """Отправляет один запрос через SOCKS5 прокси и возвращает результат"""
        start_time = time.time()
        
        try:
            async with session.get(
                self.target_url, 
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                end_time = time.time()
                response_time = end_time - start_time
                
                # Читаем содержимое ответа для диагностики ошибок
                response_text = ""
                if response.status >= 400:
                    try:
                        response_text = await response.text()
                        # Обрезаем для вывода (первые 200 символов)
                        response_text = response_text[:200] + "..." if len(response_text) > 200 else response_text
                    except Exception as e:
                        response_text = f"Не удалось прочитать содержимое ответа: {str(e)}"
                
                error_type = self._classify_error(response.status)
                
                result = {
                    'request_id': request_id,
                    'success': response.status == 200,
                    'rate_limited_429': response.status == 429,
                    'status_code': response.status,
                    'response_time': response_time,
                    'error_type': error_type,
                    'error_details': response_text if response.status >= 400 else None,
                    'timestamp': time.time(),
                    'headers': dict(response.headers) if response.status >= 400 else None
                }
                
                return result
                
        except asyncio.TimeoutError:
            end_time = time.time()
            response_time = end_time - start_time
            
            return {
                'request_id': request_id,
                'success': False,
                'rate_limited_429': False,
                'status_code': None,
                'response_time': response_time,
                'error_type': 'timeout',
                'error_details': 'Request timeout',
                'timestamp': time.time(),
                'exception_type': 'asyncio.TimeoutError'
            }
            
        except aiohttp.ClientConnectorError as e:
            end_time = time.time()
            response_time = end_time - start_time
            
            return {
                'request_id': request_id,
                'success': False,
                'rate_limited_429': False,
                'status_code': None,
                'response_time': response_time,
                'error_type': 'connection',
                'error_details': str(e),
                'timestamp': time.time(),
                'exception_type': 'aiohttp.ClientConnectorError',
                'exception_str': str(e)
            }
            
        except Exception as e:
            end_time = time.time()
            response_time = end_time - start_time
            
            error_type = type(e).__name__
            error_details = str(e)
            stack_trace = traceback.format_exc()
            
            return {
                'request_id': request_id,
                'success': False,
                'rate_limited_429': False,
                'status_code': None,
                'response_time': response_time,
                'error_type': f'other_{error_type}',
                'error_details': error_details,
                'timestamp': time.time(),
                'exception_type': error_type,
                'exception_str': error_details,
                'stack_trace': stack_trace
            }
    
    async def update_stats(self, result: Dict[str, Any]):
        """Обновляет статистику на основе результата запроса"""
        async with self.stats_lock:
            self.total_requests += 1
            current_time = time.time()
            
            # Добавляем временную метку запроса
            self.request_timestamps.append(current_time)
            
            # Обновляем счетчики по типам результатов
            if result['success']:
                self.successful_requests += 1
                self.success_timestamps.append(current_time)
                self.response_times.append(result['response_time'])
            elif result['rate_limited_429']:
                self.rate_limited_429 += 1
                self.error_timestamps.append((current_time, 'rate_limited'))
            elif result['error_type'] == 'connection':
                self.connection_errors += 1
                self.error_timestamps.append((current_time, 'connection'))
            elif result['error_type'] == 'timeout':
                self.timeout_errors += 1
                self.error_timestamps.append((current_time, 'timeout'))
            else:
                self.other_errors += 1
                self.error_timestamps.append((current_time, result['error_type']))
            
            # Обновляем статистику статус кодов
            if result['status_code']:
                self.status_codes[result['status_code']] = self.status_codes.get(result['status_code'], 0) + 1
            
            # Сохраняем детальную информацию об ошибках
            if not result['success']:
                # Увеличиваем счетчик для данного типа ошибки
                error_key = result.get('error_type', 'unknown')
                error_detail = result.get('error_details', 'No details')
                
                # Сохраняем детальную информацию о типе ошибки
                self.error_details_counter[error_key] += 1
                
                # Сохраняем примеры ошибок (не более max_error_samples для каждого типа)
                if error_key not in self.error_samples:
                    self.error_samples[error_key] = []
                
                if len(self.error_samples[error_key]) < self.max_error_samples:
                    error_sample = {
                        'timestamp': current_time,
                        'details': error_detail,
                        'exception_type': result.get('exception_type', 'None'),
                        'request_id': result.get('request_id', -1)
                    }
                    if 'headers' in result and result['headers']:
                        error_sample['headers'] = result['headers']
                    if 'stack_trace' in result and result['stack_trace']:
                        error_sample['stack_trace'] = result['stack_trace']
                    
                    self.error_samples[error_key].append(error_sample)
                
                # Для HTTP ошибок собираем более детальную информацию
                if result['status_code'] and result['status_code'] >= 400:
                    status = result['status_code']
                    if status not in self.http_error_details:
                        self.http_error_details[status] = []
                    
                    if len(self.http_error_details[status]) < self.max_error_samples:
                        self.http_error_details[status].append({
                            'timestamp': current_time,
                            'details': error_detail,
                            'headers': result.get('headers', {}),
                            'request_id': result.get('request_id', -1)
                        })
            
            # Очищаем старые временные метки (старше 1 минуты)
            cutoff_time = current_time - 60
            self.request_timestamps = [t for t in self.request_timestamps if t > cutoff_time]
            self.success_timestamps = [t for t in self.success_timestamps if t > cutoff_time]
            self.error_timestamps = [(t, e) for t, e in self.error_timestamps if t > cutoff_time]
    
    def calculate_rpm(self) -> tuple[float, float]:
        """Рассчитывает RPM для всех запросов и успешных запросов"""
        current_time = time.time()
        
        # RPM для всех запросов за последнюю минуту
        total_rpm = len(self.request_timestamps)
        
        # RPM для успешных запросов за последнюю минуту
        success_rpm = len(self.success_timestamps)
        
        return total_rpm, success_rpm
    
    async def worker(self, worker_id: int, target_requests: int):
        """Рабочий поток для отправки запросов"""
        requests_sent = 0
        
        while True:
            # Проверяем, достигнута ли цель
            async with self.stats_lock:
                if self.total_requests >= target_requests:
                    break
            
            try:
                # Создаем сессию с SOCKS5 прокси
                connector = ProxyConnector.from_url(
                    self.get_proxy_url(),
                    limit=0,  # Убираем ограничения на количество соединений
                    limit_per_host=0,  # Убираем ограничения на хост
                    ttl_dns_cache=300,  # Кеш DNS на 5 минут
                    use_dns_cache=True,
                )
                
                async with aiohttp.ClientSession(connector=connector) as session:
                    result = await self.send_request(session, self.total_requests + 1)
                    
                    # Обновляем статистику
                    await self.update_stats(result)
                    
                    # Сохраняем результат для анализа
                    async with self.stats_lock:
                        self.results.append(result)
                    
                    requests_sent += 1
                    
                    # Небольшая пауза при получении 429 для предотвращения спама
                    if result['rate_limited_429']:
                        await asyncio.sleep(1.0)
                
            except Exception as e:
                print(f"[Воркер {worker_id}] Критическая ошибка: {e}")
                await asyncio.sleep(0.5)
        
        print(f"[Воркер {worker_id}] Завершил работу, отправлено запросов: {requests_sent}")
    
    async def run_test(self, target_requests: int = 1000, concurrent_workers: int = 10):
        """Запускает тестирование с использованием ротируемого SOCKS5 прокси"""
        print(f"Начинаем тестирование ротируемого SOCKS5 прокси")
        print(f"Прокси: {self.get_proxy_url()}")
        print(f"Целевой URL: {self.target_url}")
        print(f"Целевое количество запросов: {target_requests}")
        print(f"Количество параллельных воркеров: {concurrent_workers}")
        print("-" * 60)
        
        # Сбрасываем все счетчики
        async with self.stats_lock:
            self.total_requests = 0
            self.successful_requests = 0
            self.rate_limited_429 = 0
            self.connection_errors = 0
            self.timeout_errors = 0
            self.other_errors = 0
            self.request_timestamps = []
            self.success_timestamps = []
            self.response_times = []
            self.status_codes = {}
            self.results = []
        
        start_time = time.time()
        
        # Создаем задачу для вывода статистики
        async def print_stats():
            while True:
                await asyncio.sleep(5)
                
                async with self.stats_lock:
                    if self.total_requests >= target_requests:
                        break
                    
                    current_time = time.time()
                    elapsed_time = current_time - start_time
                    
                    # Рассчитываем RPM
                    total_rpm, success_rpm = self.calculate_rpm()
                    
                    # Общий RPM за все время
                    overall_rpm = (self.total_requests / elapsed_time) * 60 if elapsed_time > 0 else 0
                    success_overall_rpm = (self.successful_requests / elapsed_time) * 60 if elapsed_time > 0 else 0
                    
                    print(f"\n{'='*60}")
                    print(f"Прогресс: {self.total_requests}/{target_requests} запросов")
                    print(f"Время работы: {elapsed_time:.1f} сек")
                    print(f"{'='*60}")
                    
                    print(f"📊 СТАТИСТИКА ЗАПРОСОВ:")
                    print(f"  ✅ Успешные:        {self.successful_requests:>6} ({self.successful_requests/max(self.total_requests,1)*100:.1f}%)")
                    print(f"  🚫 429 (лимит):     {self.rate_limited_429:>6} ({self.rate_limited_429/max(self.total_requests,1)*100:.1f}%)")
                    print(f"  🔌 Соединение:      {self.connection_errors:>6} ({self.connection_errors/max(self.total_requests,1)*100:.1f}%)")
                    print(f"  ⏱️  Таймауты:        {self.timeout_errors:>6} ({self.timeout_errors/max(self.total_requests,1)*100:.1f}%)")
                    print(f"  ❌ Другие ошибки:   {self.other_errors:>6} ({self.other_errors/max(self.total_requests,1)*100:.1f}%)")
                    
                    print(f"\n📈 RPM (запросов в минуту):")
                    print(f"  За последнюю минуту - Всего: {total_rpm:.1f}, Успешных: {success_rpm:.1f}")
                    print(f"  За все время - Всего: {overall_rpm:.1f}, Успешных: {success_overall_rpm:.1f}")
                    
                    if self.response_times:
                        avg_response_time = statistics.mean(self.response_times)
                        print(f"\n⏱️  Среднее время ответа: {avg_response_time*1000:.2f} мс")
                    
                    if self.status_codes:
                        print(f"\n📋 Статус коды:")
                        for code, count in sorted(self.status_codes.items()):
                            print(f"    {code}: {count} раз")
        
        # Запускаем задачу статистики
        stats_task = asyncio.create_task(print_stats())
        
        # Запускаем рабочие потоки
        workers = []
        for i in range(concurrent_workers):
            workers.append(asyncio.create_task(self.worker(i, target_requests)))
        
        # Ждем завершения всех рабочих или достижения цели
        try:
            await asyncio.gather(*workers)
        except asyncio.CancelledError:
            print("Тестирование было отменено.")
        finally:
            # Отменяем все оставшиеся задачи
            stats_task.cancel()
            for worker in workers:
                worker.cancel()
            
            # Ждем завершения отмененных задач
            await asyncio.gather(*workers, stats_task, return_exceptions=True)
        
        # Финальная статистика
        await self.print_final_stats(start_time)
    
    async def print_final_stats(self, start_time: float):
        """Выводит финальную статистику"""
        total_time = time.time() - start_time
        
        async with self.stats_lock:
            print(f"\n{'='*60}")
            print("🎯 ФИНАЛЬНАЯ СТАТИСТИКА")
            print(f"{'='*60}")
            
            print(f"⏱️  Общее время работы: {total_time:.1f} секунд")
            print(f"📊 Всего запросов: {self.total_requests}")
            
            print(f"\n📈 РЕЗУЛЬТАТЫ:")
            print(f"  ✅ Успешные запросы:    {self.successful_requests:>6} ({self.successful_requests/max(self.total_requests,1)*100:.1f}%)")
            print(f"  🚫 429 (лимит):         {self.rate_limited_429:>6} ({self.rate_limited_429/max(self.total_requests,1)*100:.1f}%)")
            print(f"  🔌 Ошибки соединения:   {self.connection_errors:>6} ({self.connection_errors/max(self.total_requests,1)*100:.1f}%)")
            print(f"  ⏱️  Таймауты:            {self.timeout_errors:>6} ({self.timeout_errors/max(self.total_requests,1)*100:.1f}%)")
            print(f"  ❌ Другие ошибки:       {self.other_errors:>6} ({self.other_errors/max(self.total_requests,1)*100:.1f}%)")
            
            # RPM статистика
            overall_rpm = (self.total_requests / total_time) * 60 if total_time > 0 else 0
            success_rpm = (self.successful_requests / total_time) * 60 if total_time > 0 else 0
            
            print(f"\n📈 RPM (запросов в минуту):")
            print(f"  Общий RPM: {overall_rpm:.2f}")
            print(f"  RPM успешных запросов: {success_rpm:.2f}")
            
            # Статистика времени ответа
            if self.response_times:
                print(f"\n⏱️  ВРЕМЯ ОТВЕТА (только успешные запросы):")
                print(f"  Среднее: {statistics.mean(self.response_times)*1000:.2f} мс")
                print(f"  Медиана: {statistics.median(self.response_times)*1000:.2f} мс")
                print(f"  Минимальное: {min(self.response_times)*1000:.2f} мс")
                print(f"  Максимальное: {max(self.response_times)*1000:.2f} мс")
                
                if len(self.response_times) > 1:
                    print(f"  Стандартное отклонение: {statistics.stdev(self.response_times)*1000:.2f} мс")
            
            # Детальная статистика статус кодов
            if self.status_codes:
                print(f"\n📋 СТАТУС КОДЫ:")
                total_with_status = sum(self.status_codes.values())
                for code, count in sorted(self.status_codes.items()):
                    percentage = (count / total_with_status) * 100 if total_with_status > 0 else 0
                    print(f"  {code}: {count:>6} раз ({percentage:.1f}%)")
            
            # Расширенная статистика ошибок
            if self.error_details_counter:
                print(f"\n🚨 ДЕТАЛЬНЫЙ АНАЛИЗ ОШИБОК:")
                print(f"{'='*60}")
                
                # Группируем по категориям
                error_categories = {}
                for error_key, count in sorted(self.error_details_counter.items(), key=lambda x: x[1], reverse=True):
                    if error_key.startswith("client_error_"):
                        category = "HTTP Client Errors (4xx)"
                    elif error_key.startswith("server_error_"):
                        category = "HTTP Server Errors (5xx)"
                    elif error_key == "timeout":
                        category = "Timeout Errors"
                    elif error_key == "connection":
                        category = "Connection Errors"
                    elif error_key == "rate_limited":
                        category = "Rate Limit Errors (429)"
                    else:
                        category = "Other Errors"
                    
                    if category not in error_categories:
                        error_categories[category] = []
                    
                    error_categories[category].append((error_key, count))
                
                # Выводим статистику по категориям
                for category, errors in error_categories.items():
                    print(f"\n➡️ {category}:")
                    category_total = sum(count for _, count in errors)
                    
                    for error_key, count in sorted(errors, key=lambda x: x[1], reverse=True):
                        percentage = (count / self.total_requests) * 100
                        print(f"  {error_key}: {count:>6} раз ({percentage:.1f}% от всех запросов)")
                        
                        # Выводим примеры ошибок
                        if error_key in self.error_samples and self.error_samples[error_key]:
                            print(f"    📝 Пример ошибки:")
                            sample = self.error_samples[error_key][0]
                            print(f"      ID запроса: {sample.get('request_id', 'н/д')}")
                            print(f"      Тип исключения: {sample.get('exception_type', 'н/д')}")
                            details = sample.get('details', '')
                            if len(details) > 100:
                                details = details[:100] + "..."
                            print(f"      Детали: {details}")
                
                # Сохраняем подробный отчет в JSON
                try:
                    detailed_report = {
                        "summary": {
                            "total_requests": self.total_requests,
                            "successful": self.successful_requests,
                            "rate_limited": self.rate_limited_429,
                            "connection_errors": self.connection_errors,
                            "timeout_errors": self.timeout_errors,
                            "other_errors": self.other_errors
                        },
                        "status_codes": self.status_codes,
                        "error_details": {k: v for k, v in self.error_details_counter.items()},
                        "error_samples": self.error_samples
                    }
                    
                    with open("proxy_test_detailed_report.json", "w", encoding="utf-8") as f:
                        json.dump(detailed_report, f, ensure_ascii=False, indent=2)
                    
                    print(f"\n📊 Подробный отчет об ошибках сохранен в файл: proxy_test_detailed_report.json")
                except Exception as e:
                    print(f"Не удалось сохранить подробный отчет: {e}")

async def main():
    target_url = "https://steamcommunity.com/market/search?appid=730"
    
    tester = RotatedProxyTester(target_url)

    # Запускаем тестирование с ротируемым SOCKS5 прокси
    await tester.run_test(target_requests=5000, concurrent_workers=300)
    # 60 потоков - 4551 успешно
    # 40 потоков - 4555 успешно успешный рпм - 883
    # 70 потоков - 4548 успешно успешный рпм - 1413
    # 100 потоков - 4655 успешно успешный рпм - 1881
    # 150 потоков - 4578 успешно успешный рпм - 2200
    # 200 потоков - 4593 успешно успешный рпм - 2399
    # 300 потоков - 4509 успешно успешный рпм - 2563 - на 1 ядре
    # 300 потоков - 4492 успешно успешный рпм - 3086 - на 2х ядрах. до этого 1 было
    # 300 потоков - 4608 успешно успешный рпм - 3035 - на 4х ядрах и 8 гигах. до этого 2 ядра 4 гига было


if __name__ == "__main__":
    # Устанавливаем политику цикла событий для Windows перед запуском
    if platform.system() == 'Windows':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
