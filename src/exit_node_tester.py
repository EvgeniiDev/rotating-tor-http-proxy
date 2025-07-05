import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Set, Optional
import requests
from datetime import datetime

from tor_process import TorProcess
from config_manager import ConfigManager

logger = logging.getLogger(__name__)

class ExitNodeTester:
    """
    Модуль для тестирования выходных узлов Tor.
    Проверяет узлы на работоспособность через HTTP-запросы к Steam Community Market.
    """
    
    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.test_url = "https://steamcommunity.com/market/search?appid=730"
        self.required_success_count = 4  # Минимум 4 успешных ответа из 6
        self.test_requests_count = 6     # Количество тестовых запросов
        self.max_workers = 10            # Количество потоков для тестирования
        self.timeout = 30                # Таймаут для HTTP-запросов в секундах
        
    def test_exit_nodes(self, exit_nodes: List[str]) -> List[str]:
        """
        Тестирует список выходных узлов и возвращает только рабочие.
        
        Args:
            exit_nodes: Список IP-адресов выходных узлов для тестирования
            
        Returns:
            Список IP-адресов узлов, прошедших тестирование
        """
        if not exit_nodes:
            logger.warning("No exit nodes provided for testing")
            return []
            
        logger.info(f"Starting testing of {len(exit_nodes)} exit nodes")
        
        # Создаем временный Tor процесс для тестирования
        test_port = self._get_test_port()
        test_instance = TorProcess(port=test_port, exit_nodes=exit_nodes)
        
        if not test_instance.create_config(self.config_manager):
            logger.error(f"Failed to create config for test instance on port {test_port}")
            return []
            
        if not test_instance.start_process():
            logger.error(f"Failed to start test instance on port {test_port}")
            test_instance.cleanup()
            return []
            
        try:
            # Ждем запуска процесса
            if not self._wait_for_startup(test_instance):
                logger.error(f"Test instance on port {test_port} failed to start properly")
                return []
                
            logger.info(f"Test instance started successfully on port {test_port}")
            
            # Тестируем узлы
            working_nodes = self._test_nodes_with_threads(test_instance, exit_nodes)
            
            logger.info(f"Testing completed. {len(working_nodes)}/{len(exit_nodes)} nodes passed the test")
            return working_nodes
            
        finally:
            # Очищаем временный процесс
            test_instance.stop_process()
            test_instance.cleanup()
            logger.info(f"Test instance on port {test_port} cleaned up")
    
    def _get_test_port(self) -> int:
        """Генерирует уникальный порт для тестового процесса"""
        return 20000 + int(time.time() % 10000)
    
    def _wait_for_startup(self, instance: TorProcess, timeout: int = 60) -> bool:
        """Ждет запуска Tor процесса"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if instance.test_connection():
                return True
            time.sleep(2)
        return False
    
    def _test_nodes_with_threads(self, instance: TorProcess, exit_nodes: List[str]) -> List[str]:
        """
        Тестирует узлы в многопоточном режиме.
        
        Args:
            instance: Экземпляр TorProcess для тестирования
            exit_nodes: Список узлов для тестирования
            
        Returns:
            Список рабочих узлов
        """
        working_nodes = []
        lock = threading.Lock()
        
        def test_single_node(node_ip: str) -> Optional[str]:
            """Тестирует один узел"""
            try:
                success_count = 0
                
                # Обновляем конфигурацию для использования только одного узла
                if not instance.reload_exit_nodes([node_ip], self.config_manager):
                    logger.warning(f"Failed to reload exit nodes for {node_ip}")
                    return None
                
                # Ждем немного для применения изменений
                time.sleep(3)
                
                # Выполняем тестовые запросы
                for i in range(self.test_requests_count):
                    try:
                        response = requests.get(
                            self.test_url,
                            proxies=instance.get_proxies(),
                            timeout=self.timeout
                        )
                        
                        if response.status_code == 200:
                            success_count += 1
                            logger.debug(f"Node {node_ip}: Request {i+1} successful (200)")
                        else:
                            logger.debug(f"Node {node_ip}: Request {i+1} failed (HTTP {response.status_code})")
                            
                    except requests.RequestException as e:
                        logger.debug(f"Node {node_ip}: Request {i+1} failed with exception: {e}")
                        continue
                
                # Проверяем результат
                if success_count >= self.required_success_count:
                    logger.info(f"Node {node_ip} passed test: {success_count}/{self.test_requests_count} successful requests")
                    return node_ip
                else:
                    logger.info(f"Node {node_ip} failed test: {success_count}/{self.test_requests_count} successful requests")
                    return None
                    
            except Exception as e:
                logger.error(f"Error testing node {node_ip}: {e}")
                return None
        
        # Запускаем тестирование в пуле потоков
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Создаем задачи для всех узлов
            future_to_node = {
                executor.submit(test_single_node, node): node 
                for node in exit_nodes
            }
            
            # Обрабатываем результаты по мере завершения
            for future in as_completed(future_to_node):
                node = future_to_node[future]
                try:
                    result = future.result()
                    if result:
                        with lock:
                            working_nodes.append(result)
                except Exception as e:
                    logger.error(f"Exception occurred while testing node {node}: {e}")
        
        return working_nodes
    
    def test_and_filter_nodes(self, exit_nodes: List[str]) -> List[str]:
        """
        Тестирует и фильтрует узлы, возвращая только рабочие.
        Удобный метод для быстрого использования.
        
        Args:
            exit_nodes: Список узлов для тестирования
            
        Returns:
            Отфильтрованный список рабочих узлов
        """
        start_time = time.time()
        working_nodes = self.test_exit_nodes(exit_nodes)
        elapsed_time = time.time() - start_time
        
        logger.info(f"Node testing completed in {elapsed_time:.2f} seconds")
        logger.info(f"Working nodes: {working_nodes}")
        
        return working_nodes
    
    def get_test_stats(self) -> Dict:
        """Возвращает статистику тестирования"""
        return {
            'test_url': self.test_url,
            'required_success_count': self.required_success_count,
            'test_requests_count': self.test_requests_count,
            'max_workers': self.max_workers,
            'timeout': self.timeout
        } 