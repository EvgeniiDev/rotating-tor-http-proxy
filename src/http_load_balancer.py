
import logging
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from typing import List, Dict, Optional
import requests
from statistics_manager import StatisticsManager

logger = logging.getLogger(__name__)


class LoadBalancerHTTPServer(HTTPServer):
    """Расширенный HTTP сервер с атрибутом для балансировщика"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_balancer: Optional['HTTPLoadBalancer'] = None


class LoadBalancerHandler(BaseHTTPRequestHandler):
    """HTTP request handler для балансировщика нагрузки"""
    
    def do_GET(self):
        self._handle_request()
    
    def do_POST(self):
        self._handle_request()
    
    def do_PUT(self):
        self._handle_request()
    
    def do_DELETE(self):
        self._handle_request()
    
    def do_PATCH(self):
        self._handle_request()
    
    def do_HEAD(self):
        self._handle_request()
    
    def _handle_request(self):
        """Обработка HTTP запроса с балансировкой нагрузки через SOCKS5"""
        try:
            # Получаем следующий доступный прокси
            load_balancer = getattr(self.server, 'load_balancer', None)
            if not load_balancer:
                self._send_error_response(503, "Load balancer not available")
                return
                
            proxy_port = load_balancer.get_next_proxy()
            
            if not proxy_port:
                self._send_error_response(503, "No available proxy servers")
                return
            
            content_length = int(self.headers.get('Content-Length', 0))
            request_body = self.rfile.read(content_length) if content_length > 0 else b''
            
            # Подготавливаем заголовки для прокси-запроса
            headers = dict(self.headers)
            # Удаляем заголовки, которые не должны передаваться
            headers.pop('Host', None)
            headers.pop('Connection', None)
            headers.pop('Proxy-Connection', None)
            
            # Парсим оригинальный URL
            if self.path.startswith('http'):
                # Абсолютный URL (для прокси-запросов)
                parsed_url = urlparse(self.path)
                target_host = parsed_url.hostname
                target_port = parsed_url.port or (443 if parsed_url.scheme == 'https' else 80)
                target_path = parsed_url.path + ('?' + parsed_url.query if parsed_url.query else '')
                scheme = parsed_url.scheme
            else:
                # Относительный URL - используем Host заголовок
                host_header = self.headers.get('Host', '')
                if ':' in host_header:
                    target_host, port_str = host_header.split(':', 1)
                    target_port = int(port_str)
                else:
                    target_host = host_header
                    target_port = 80
                target_path = self.path
                scheme = 'https' if target_port == 443 else 'http'
            try:
                session = load_balancer.get_proxy_session(proxy_port)
                if not session:
                    self._send_error_response(503, f"No session available for proxy {proxy_port}")
                    return
                
                if target_port in [80, 443] and ((scheme == 'http' and target_port == 80) or (scheme == 'https' and target_port == 443)):
                    target_url = f"{scheme}://{target_host}{target_path}"
                else:
                    target_url = f"{scheme}://{target_host}:{target_port}{target_path}"
                
                # Отправляем запрос через готовую SOCKS5 сессию
                response = session.request(
                    method=self.command,
                    url=target_url,
                    headers=headers,
                    data=request_body,
                    timeout=30,
                    allow_redirects=False,
                    verify=False  # Отключаем проверку SSL для Tor
                )

                if load_balancer:
                    load_balancer.stats_manager.record_request(proxy_port, True, response.status_code)
                
                self.send_response(response.status_code)
                
                for header, value in response.headers.items():
                    if header.lower() not in ['connection', 'transfer-encoding', 'content-encoding']:
                        self.send_header(header, value)
                
                self.end_headers()
                
                if response.content:
                    self.wfile.write(response.content)
                
            except requests.RequestException as e:
                logger.error(f"Proxy request failed for port {proxy_port}: {e}")
                if load_balancer:
                    load_balancer.stats_manager.record_request(proxy_port, False, 0)
                    
                    load_balancer.mark_proxy_unavailable(proxy_port)
                
                self._send_error_response(502, f"Proxy server error: {str(e)}")
                
        except Exception as e:
            logger.error(f"Load balancer error: {e}")
            self._send_error_response(500, "Internal server error")
    
    def _send_error_response(self, status_code: int, message: str):
        try:
            self.send_response(status_code)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(message.encode('utf-8'))
        except:
            pass
    
    def log_message(self, format, *args):
        pass


class HTTPLoadBalancer:
    """Оптимизированный HTTP балансировщик нагрузки с persistent connections и разделением на доступные/недоступные прокси"""
    def __init__(self, listen_port: int = 8080):
        self.listen_port = listen_port
        # Разделенные списки для максимально быстрого доступа
        self.available_proxies: List[int] = []  # Только доступные прокси для round-robin
        self.unavailable_proxies: List[int] = []  # Недоступные прокси для проверки восстановления
        # Persistent connections - создаются при добавлении, живут до завершения работы
        self.proxy_sessions: Dict[int, requests.Session] = {}
        self.current_index = 0
        self.lock = threading.Lock()
        self.server: Optional[HTTPServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.stats_manager = StatisticsManager()
        self.last_health_check = 0
        self.health_check_interval = 30  # Проверяем здоровье недоступных прокси каждые 30 секунд
    
    def add_proxy(self, port: int):
        with self.lock:
            if port in self.proxy_sessions:
                logger.debug(f"Proxy {port} already exists, skipping")
                return
            
            session = requests.Session()
            session.proxies = {
                'http': f'socks5://127.0.0.1:{port}',
                'https': f'socks5://127.0.0.1:{port}'
            }
            
            self.proxy_sessions[port] = session
            self.stats_manager.add_proxy(port)
            
            if self._test_proxy_connection(session, port):
                self.available_proxies.append(port)
                logger.info(f"Added available SOCKS5 proxy with persistent connection on port {port}")
            else:
                self.unavailable_proxies.append(port)
                logger.warning(f"Added unavailable SOCKS5 proxy on port {port} (will retry connection)")
    
    def remove_proxy(self, port: int):
        with self.lock:
            if port in self.available_proxies:
                self.available_proxies.remove(port)
                
            if port in self.unavailable_proxies:
                self.unavailable_proxies.remove(port)
                
            if port in self.proxy_sessions:
                try:
                    self.proxy_sessions[port].close()
                    logger.debug(f"Closed persistent connection for proxy {port}")
                except Exception as e:
                    logger.debug(f"Error closing persistent connection for proxy {port}: {e}")
                del self.proxy_sessions[port]
                
            self.stats_manager.remove_proxy(port)
            logger.info(f"Removed SOCKS5 proxy with persistent connection on port {port}")
    
    def _test_proxy_connection(self, session: requests.Session, port: int) -> bool:
        try:
            # Быстрая проверка через httpbin
            response = session.get('http://httpbin.org/ip', timeout=10)
            return response.status_code == 200
        except:
            return False
    
    def get_next_proxy(self) -> Optional[int]:
        """Максимально быстрое получение следующего доступного прокси (round-robin)"""
        with self.lock:
            # Если нет доступных прокси, проверяем недоступные
            if not self.available_proxies:
                current_time = time.time()
                if current_time - self.last_health_check > self.health_check_interval:
                    self._check_proxy_health()
                    self.last_health_check = current_time
                
                # Если все еще нет доступных прокси
                if not self.available_proxies:
                    logger.warning("No available SOCKS5 proxy servers")
                    return None
            
            # Простой round-robin без дополнительных проверок для максимальной скорости
            proxy_port = self.available_proxies[self.current_index]
            self.current_index = (self.current_index + 1) % len(self.available_proxies)
            
            return proxy_port
    
    def get_proxy_session(self, port: int) -> Optional[requests.Session]:
        return self.proxy_sessions.get(port)
    
    def mark_proxy_unavailable(self, port: int):
        with self.lock:
            if port in self.available_proxies:
                self.available_proxies.remove(port)
                if port not in self.unavailable_proxies:
                    self.unavailable_proxies.append(port)
                logger.warning(f"Moved SOCKS5 proxy {port} to unavailable list (keeping persistent connection)")
                
                # Корректируем индекс если нужно
                if self.current_index >= len(self.available_proxies) and self.available_proxies:
                    self.current_index = 0
    
    def _check_proxy_health(self):
        if not self.unavailable_proxies:
            return
        
        # Проверяем только несколько прокси за раз для избежания блокировки
        max_check_per_cycle = min(3, len(self.unavailable_proxies))
        proxies_to_check = self.unavailable_proxies[:max_check_per_cycle]
        
        for port in proxies_to_check:
            try:
                session = self.proxy_sessions.get(port)
                if session and self._test_proxy_connection(session, port):
                    self.unavailable_proxies.remove(port)
                    if port not in self.available_proxies:
                        self.available_proxies.append(port)
                    logger.info(f"SOCKS5 proxy {port} is back online")
            except Exception as e:
                logger.debug(f"Proxy {port} still unavailable: {e}")
    
    def start(self):
        """Запуск HTTP балансировщика"""
        if self.server_thread and self.server_thread.is_alive():
            logger.warning("HTTP Load balancer is already running")
            return
        try:
            self.server = LoadBalancerHTTPServer(('0.0.0.0', self.listen_port), LoadBalancerHandler)
            self.server.load_balancer = self
            
            self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.server_thread.start()
            
            logger.info(f"HTTP Load Balancer started on port {self.listen_port} (proxying to SOCKS5)")
        except Exception as e:
            logger.error(f"Failed to start HTTP load balancer: {e}")
            raise
    
    def stop(self):
        """Остановка HTTP балансировщика и закрытие всех persistent connections"""
        logger.info("Stopping HTTP Load Balancer...")
        
        # Останавливаем HTTP сервер
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            
        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=5)
        
        # Закрываем все persistent proxy sessions (только здесь!)
        with self.lock:
            logger.info(f"Closing {len(self.proxy_sessions)} persistent proxy connections...")
            for port, session in self.proxy_sessions.items():
                try:
                    session.close()
                    logger.debug(f"Closed persistent session for proxy {port}")
                except Exception as e:
                    logger.debug(f"Error closing persistent session for proxy {port}: {e}")
            
            # Очищаем все списки и данные
            self.proxy_sessions.clear()
            self.available_proxies.clear()
            self.unavailable_proxies.clear()
            self.current_index = 0
        
        logger.info("HTTP Load Balancer stopped successfully")
    
    def get_stats(self) -> Dict:
        all_proxies = self.available_proxies + self.unavailable_proxies
        return {
            'total_proxies': len(all_proxies),
            'available_proxies': len(self.available_proxies),
            'unavailable_proxies': len(self.unavailable_proxies),
            'available_proxy_ports': self.available_proxies.copy(),
            'unavailable_proxy_ports': self.unavailable_proxies.copy(),
            'current_index': self.current_index,
            'listen_port': self.listen_port,
            'proxy_stats': self.stats_manager.get_all_stats()
        }
    
    def get_proxy_list(self) -> List[int]:
        return self.available_proxies + self.unavailable_proxies