import time
import threading
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class SimplifiedProxyStats:
    """Упрощенная статистика для одного прокси-сервера - только успешные/неуспешные запросы"""
    
    def __init__(self, port: int):
        self.port = port
        self.successful_requests = 0
        self.failed_requests = 0
        self.created_at = time.time()
        self.last_request_time = None
        self.lock = threading.Lock()
    
    def record_request(self, success: bool, status_code: int = 0):
        """Записать результат запроса"""
        with self.lock:
            if success:
                self.successful_requests += 1
            else:
                self.failed_requests += 1
            self.last_request_time = time.time()
    
    def get_stats(self) -> Dict:
        """Получить статистику прокси"""
        with self.lock:
            total_requests = self.successful_requests + self.failed_requests
            success_rate = (self.successful_requests / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'port': self.port,
                'successful_requests': self.successful_requests,
                'failed_requests': self.failed_requests,
                'total_requests': total_requests,
                'success_rate': round(success_rate, 2),
                'created_at': self.created_at,
                'last_request_time': self.last_request_time,
                'uptime_seconds': round(time.time() - self.created_at, 2)
            }


class StatisticsManager:
    """Упрощенный менеджер статистики - только успешные/неуспешные запросы"""
    
    def __init__(self):
        self.proxy_stats: Dict[int, SimplifiedProxyStats] = {}
        self.lock = threading.Lock()
        logger.info("Initialized simplified statistics manager")
    
    def add_proxy(self, port: int):
        """Добавить прокси для отслеживания статистики"""
        with self.lock:
            if port not in self.proxy_stats:
                self.proxy_stats[port] = SimplifiedProxyStats(port)
                logger.info(f"Added proxy {port} to statistics tracking")
    
    def remove_proxy(self, port: int):
        """Удалить прокси из отслеживания статистики"""
        with self.lock:
            if port in self.proxy_stats:
                stats = self.proxy_stats[port].get_stats()
                del self.proxy_stats[port]
                logger.info(f"Removed proxy {port} from statistics tracking. Final stats: {stats['total_requests']} requests, {stats['success_rate']}% success rate")
    
    def record_request(self, port: int, success: bool, status_code: int = 0):
        """Записать результат запроса для прокси"""
        with self.lock:
            if port in self.proxy_stats:
                self.proxy_stats[port].record_request(success, status_code)
    
    def get_proxy_stats(self, port: int) -> Dict:
        """Получить статистику конкретного прокси"""
        with self.lock:
            if port in self.proxy_stats:
                return self.proxy_stats[port].get_stats()
            return {}
    
    def get_all_stats(self) -> Dict:
        """Получить статистику всех прокси"""
        with self.lock:
            all_stats = {}
            for port, proxy_stats in self.proxy_stats.items():
                all_stats[port] = proxy_stats.get_stats()
            return all_stats
    
    def get_summary_stats(self) -> Dict:
        """Получить общую статистику"""
        with self.lock:
            total_proxies = len(self.proxy_stats)
            total_requests = 0
            total_successful = 0
            total_failed = 0
            
            for proxy_stats in self.proxy_stats.values():
                stats = proxy_stats.get_stats()
                total_requests += stats['total_requests']
                total_successful += stats['successful_requests']
                total_failed += stats['failed_requests']
            
            overall_success_rate = (total_successful / total_requests * 100) if total_requests > 0 else 0
            
            return {
                'total_proxies': total_proxies,
                'total_requests': total_requests,
                'successful_requests': total_successful,
                'failed_requests': total_failed,
                'overall_success_rate': round(overall_success_rate, 2)
            }

    def clear_all_stats(self):
        """Очистить всю статистику"""
        with self.lock:
            self.proxy_stats.clear()
            logger.info("Cleared all statistics")

