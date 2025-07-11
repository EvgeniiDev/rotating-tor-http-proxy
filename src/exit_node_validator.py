import logging
import threading
import time
import subprocess
import requests
import os
from typing import List, Dict, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue

from tor_config_builder import TorConfigBuilder
from tor_process_manager import TorProcessManager

logger = logging.getLogger(__name__)


class ExitNodeValidator:
    """
    Класс для проверки выходных нод Tor на пригодность для парсинга.
    Отправляет 6 запросов на страницу Steam и считает ноду подходящей,
    если 3 или больше запросов успешны.
    """
    
    def __init__(self, config_builder: TorConfigBuilder, max_workers: int = 25):
        self.config_builder = config_builder
        self.max_workers = max_workers
        
        # Параметры валидации
        self.test_url = "https://steamcommunity.com/market/search?appid=730"
        self.requests_per_node = 6
        self.min_successful_requests = 3
        self.request_timeout = 20
        self.max_test_time = 1800  # 30 минут максимум
        
        # Воркеры для тестирования
        self.test_workers: List[TorProcessManager] = []
        self._workers_lock = threading.Lock()
        
        # Статистика
        self.stats = {
            'total_tested': 0,
            'successful_nodes': 0,
            'failed_nodes': 0,
            'last_test_time': None
        }
        
    def validate_exit_nodes(self, exit_node_ips: List[str]) -> List[str]:
        """
        Проверяет список выходных нод на пригодность для парсинга Steam.
        
        Args:
            exit_node_ips: Список IP адресов выходных нод
            
        Returns:
            Список IP адресов нод, прошедших валидацию
        """
        if not exit_node_ips:
            logger.warning("No exit nodes provided for validation")
            return []
        
        logger.info(f"Starting validation of {len(exit_node_ips)} exit nodes")
        start_time = time.time()
        
        # Инициализируем воркеров
        if not self._initialize_workers():
            logger.error("Failed to initialize test workers")
            return []
        
        try:
            # Проводим валидацию
            valid_nodes = self._validate_nodes_parallel(exit_node_ips, start_time)
            
            # Обновляем статистику
            self.stats.update({
                'total_tested': len(exit_node_ips),
                'successful_nodes': len(valid_nodes),
                'failed_nodes': len(exit_node_ips) - len(valid_nodes),
                'last_test_time': time.time()
            })
            
            success_rate = (len(valid_nodes) / len(exit_node_ips) * 100) if exit_node_ips else 0
            elapsed_time = time.time() - start_time
            
            logger.info(f"Validation completed in {elapsed_time:.1f}s: "
                       f"{len(valid_nodes)}/{len(exit_node_ips)} nodes passed ({success_rate:.1f}%)")
            
            return valid_nodes
            
        finally:
            self._cleanup_workers()
    
    def get_validation_stats(self) -> Dict:
        """
        Возвращает статистику валидации.
        """
        return {
            **self.stats,
            'max_workers': self.max_workers,
            'test_url': self.test_url,
            'requests_per_node': self.requests_per_node,
            'min_successful_requests': self.min_successful_requests,
            'success_threshold': f"{self.min_successful_requests}/{self.requests_per_node}",
            'timeout': self.request_timeout
        }
    
    def _initialize_workers(self) -> bool:
        """
        Инициализирует пул воркеров для тестирования.
        """
        with self._workers_lock:
            if self.test_workers:
                logger.warning("Workers already initialized")
                return True
            
            logger.info(f"Initializing {self.max_workers} test workers...")
            
            # Очищаем существующие процессы Tor на портах тестирования
            self._cleanup_existing_processes()
            
            # Находим свободные порты
            start_port = 30100
            free_ports = self._find_free_ports(start_port, self.max_workers)
            
            if len(free_ports) < self.max_workers:
                logger.warning(f"Only found {len(free_ports)} free ports out of {self.max_workers} needed")
            
            # Создаем воркеров параллельно
            successful_workers = []
            with ThreadPoolExecutor(max_workers=min(len(free_ports), 10)) as executor:
                future_to_port = {
                    executor.submit(self._create_worker, port): port
                    for port in free_ports
                }
                
                for future in as_completed(future_to_port):
                    port = future_to_port[future]
                    try:
                        worker = future.result()
                        if worker:
                            successful_workers.append(worker)
                            if len(successful_workers) >= self.max_workers:
                                break
                    except Exception as e:
                        logger.error(f"Failed to create worker on port {port}: {e}")
            
            self.test_workers = successful_workers[:self.max_workers]
            
            logger.info(f"Successfully initialized {len(self.test_workers)}/{self.max_workers} workers")
            return len(self.test_workers) > 0
    
    def _cleanup_workers(self):
        """
        Очищает воркеров тестирования.
        """
        with self._workers_lock:
            for worker in self.test_workers:
                try:
                    worker.stop()
                except Exception as e:
                    logger.error(f"Error stopping worker: {e}")
            
            self.test_workers.clear()
            logger.debug("All test workers cleaned up")
    
    def _cleanup_existing_processes(self):
        """
        Убивает существующие процессы Tor на портах тестирования.
        """
        try:
            subprocess.run(
                ['pkill', '-f', 'tor.*3[0-9][0-9][0-9][0-9]'],
                capture_output=True,
                timeout=5
            )
            time.sleep(2)
        except Exception:
            pass
    
    def _find_free_ports(self, start_port: int, count: int) -> List[int]:
        """
        Находит свободные порты для воркеров.
        """
        ports = []
        port = start_port
        
        while len(ports) < count * 2 and port < start_port + 1000:  # ищем с запасом
            if self._is_port_free(port):
                ports.append(port)
            port += 1
        
        return ports[:count * 2]  # возвращаем с запасом
    
    def _is_port_free(self, port: int) -> bool:
        """
        Проверяет, свободен ли порт.
        """
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('127.0.0.1', port))
            sock.close()
            return True
        except OSError:
            return False
        finally:
            try:
                sock.close()
            except:
                pass
    
    def _create_worker(self, port: int) -> Optional[TorProcessManager]:
        """
        Создает одного воркера для тестирования.
        """
        try:
            # Создаем воркера без выходных узлов (будем их менять динамически)
            worker = TorProcessManager(port, [], self.config_builder)
            
            # Запускаем
            if worker.start():
                # Ждем готовности
                if self._wait_for_worker_ready(worker):
                    return worker
                else:
                    worker.stop()
            
            return None
            
        except Exception as e:
            logger.error(f"Error creating worker on port {port}: {e}")
            return None
    
    def _wait_for_worker_ready(self, worker: TorProcessManager, timeout: int = 30) -> bool:
        """
        Ожидает готовности воркера.
        """
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                if worker.check_health():
                    return True
            except Exception:
                pass
            
            time.sleep(1)
        
        return False
    
    def _validate_nodes_parallel(self, exit_node_ips: List[str], start_time: float) -> List[str]:
        """
        Валидирует ноды параллельно с использованием воркеров.
        """
        valid_nodes = []
        tested_count = 0
        lock = threading.Lock()
        progress_lock = threading.Lock()
        
        # Создаем очередь с нодами для тестирования
        node_queue = Queue()
        for node_ip in exit_node_ips:
            node_queue.put(node_ip)
        
        def worker_thread(worker: TorProcessManager, worker_id: int):
            nonlocal tested_count
            
            while time.time() - start_time <= self.max_test_time:
                try:
                    node_ip = node_queue.get(timeout=2)
                except:
                    break  # Очередь пуста
                
                try:
                    # Переключаем воркера на тестируемую ноду
                    if self._configure_worker_for_node(worker, node_ip):
                        # Тестируем ноду
                        if self._test_node_with_steam_requests(worker, node_ip):
                            with lock:
                                valid_nodes.append(node_ip)
                    
                    with progress_lock:
                        tested_count += 1
                        if tested_count % 10 == 0:
                            elapsed = time.time() - start_time
                            logger.info(f"Progress: {tested_count}/{len(exit_node_ips)} nodes tested "
                                      f"({elapsed:.1f}s elapsed)")
                
                except Exception as e:
                    logger.error(f"Error testing node {node_ip} on worker {worker_id}: {e}")
                
                finally:
                    node_queue.task_done()
        
        # Запускаем потоки тестирования
        threads = []
        for i, worker in enumerate(self.test_workers):
            thread = threading.Thread(
                target=worker_thread,
                args=(worker, i),
                name=f"NodeValidator-{i}"
            )
            thread.daemon = True
            thread.start()
            threads.append(thread)
        
        # Ждем завершения всех задач
        node_queue.join()
        
        # Ждем завершения потоков
        for thread in threads:
            thread.join(timeout=5)
        
        return valid_nodes
    
    def _configure_worker_for_node(self, worker: TorProcessManager, node_ip: str) -> bool:
        """
        Конфигурирует воркера для использования конкретной выходной ноды.
        """
        try:
            # Перезагружаем воркера с новой выходной нодой
            if worker.reload_exit_nodes([node_ip]):
                # Ждем применения конфигурации
                time.sleep(2)
                
                # Проверяем, что соединение работает
                return self._wait_for_worker_ready(worker, timeout=10)
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to configure worker for node {node_ip}: {e}")
            return False
    
    def _test_node_with_steam_requests(self, worker: TorProcessManager, node_ip: str) -> bool:
        """
        Тестирует ноду с помощью запросов к Steam.
        Отправляет 6 запросов и считает ноду валидной если 3+ успешны.
        """
        successful_requests = 0
        
        for request_num in range(self.requests_per_node):
            try:
                response = requests.get(
                    self.test_url,
                    proxies=worker._get_proxies(),
                    timeout=self.request_timeout,
                    headers={
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    }
                )
                
                if response.status_code == 200:
                    successful_requests += 1
                    
                    # Если уже достигли минимума, можно прекратить тестирование
                    if successful_requests >= self.min_successful_requests:
                        logger.info(f"Node {node_ip} PASSED early: "
                                  f"{successful_requests}/{request_num + 1} requests successful")
                        return True
                
            except Exception as e:
                logger.debug(f"Request {request_num + 1} failed for node {node_ip}: {e}")
                continue
        
        # Определяем результат
        is_valid = successful_requests >= self.min_successful_requests
        status = "PASSED" if is_valid else "FAILED"
        
        logger.info(f"Node {node_ip} {status}: "
                   f"{successful_requests}/{self.requests_per_node} requests successful")
        
        return is_valid