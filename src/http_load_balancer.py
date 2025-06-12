import logging
import time
import threading
import socket
import select
import random
import urllib.parse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from typing import List, Dict, Optional, Tuple
import requests
from statistics_manager import StatisticsManager

logger = logging.getLogger(__name__)


class LoadBalancerHTTPServer(ThreadingHTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.load_balancer: Optional['HTTPLoadBalancer'] = None
        self.allow_reuse_address = True


class LoadBalancerHandler(BaseHTTPRequestHandler):

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

    def do_CONNECT(self):
        self._handle_connect_request()

    def _handle_request(self):
        load_balancer = getattr(self.server, 'load_balancer', None)
        if not load_balancer:
            self._send_error_response(503, "Load balancer not available")
            return

        proxy_port = load_balancer.get_next_proxy()
        if not proxy_port:
            self._send_error_response(503, "No available proxy servers")
            return

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            request_body = self.rfile.read(
                content_length) if content_length > 0 else b''

            headers = dict(self.headers)
            for header in ['Host', 'Connection', 'Proxy-Connection']:
                headers.pop(header, None)

            target_url = self._build_target_url()
            session = load_balancer.get_proxy_session(proxy_port)

            if not session:
                self._send_error_response(
                    503, f"No session available for proxy {proxy_port}")
                return

            logger.debug(f"Making {self.command} request to {target_url} via proxy {proxy_port}")

            # Увеличенный таймаут для Tor соединений - 60 секунд вместо 30
            response = session.request(
                method=self.command,
                url=target_url,
                headers=headers,
                data=request_body,
                timeout=60,
                allow_redirects=False,
                verify=False            )

            load_balancer.stats_manager.record_request(
                proxy_port, True, response.status_code)
            
            load_balancer.mark_proxy_success(proxy_port)

            self.send_response(response.status_code)
            for header, value in response.headers.items():
                if header.lower() not in ['connection', 'transfer-encoding', 'content-encoding']:
                    self.send_header(header, value)
            self.end_headers()

            if response.content:
                self.wfile.write(response.content)

        except requests.RequestException as e:
            logger.error(f"Proxy request failed for port {proxy_port}: {e}")
            load_balancer.stats_manager.record_request(proxy_port, False, 0)
            # Увеличиваем счетчик ошибок прокси для более быстрого определения проблемных
            load_balancer.mark_proxy_unavailable(proxy_port)
            self._send_error_response(502, f"Proxy server error: {str(e)}")
        except Exception as e:
            logger.error(f"Request handling error: {e}")
            self._send_error_response(500, "Internal server error")

    def _build_target_url(self):
        if self.path.startswith('http'):
            parsed_url = urlparse(self.path)
            target_host = parsed_url.hostname
            target_port = parsed_url.port or (
                443 if parsed_url.scheme == 'https' else 80)
            target_path = parsed_url.path + \
                ('?' + parsed_url.query if parsed_url.query else '')
            scheme = parsed_url.scheme
        else:
            host_header = self.headers.get('Host', '')
            if ':' in host_header:
                target_host, port_str = host_header.split(':', 1)
                target_port = int(port_str)
            else:
                target_host = host_header
                target_port = 80
            target_path = self.path
            scheme = 'https' if target_port == 443 else 'http'

        if target_port in [80, 443] and ((scheme == 'http' and target_port == 80) or (scheme == 'https' and target_port == 443)):
            return f"{scheme}://{target_host}{target_path}"
        return f"{scheme}://{target_host}:{target_port}{target_path}"

    def _handle_connect_request(self):
        load_balancer = getattr(self.server, 'load_balancer', None)
        if not load_balancer:
            self._send_error_response(503, "Load balancer not available")
            return

        proxy_port = load_balancer.get_next_proxy()
        if not proxy_port:
            self._send_error_response(503, "No available proxy servers")
            return

        session = load_balancer.get_proxy_session(proxy_port)
        if not session:
            self._send_error_response(
                503, f"No session available for proxy {proxy_port}")
            return

        host_port = self.path
        if ':' in host_port:
            target_host, target_port = host_port.rsplit(':', 1)
            target_port = int(target_port)
        else:
            target_host = host_port
            target_port = 443

        proxy_socket = None
        try:
            logger.info(f"Establishing CONNECT tunnel to {target_host}:{target_port} via proxy {proxy_port}")
            
            proxy_socket = self._create_socks_socket_from_session(
                session, target_host, target_port)            # Отправляем успешный ответ клиенту
            self.send_response(200, 'Connection Established')
            self.send_header('Proxy-Agent', 'Tor-HTTP-Proxy/1.0')
            self.end_headers()
            
            # Записываем успешную статистику
            load_balancer.stats_manager.record_request(proxy_port, True, 200)
            
            # Сбрасываем счетчик ошибок для успешного соединения
            load_balancer.mark_proxy_success(proxy_port)
            
            # Запускаем туннелирование данных
            logger.debug(f"Starting data tunnel for {target_host}:{target_port}")
            self._tunnel_data(self.connection, proxy_socket)
            logger.debug(f"Data tunnel closed for {target_host}:{target_port}")

        except Exception as e:
            logger.error(
                f"CONNECT tunnel failed for {host_port} via proxy {proxy_port}: {e}")
            load_balancer.stats_manager.record_request(proxy_port, False, 0)
            # НЕ помечаем прокси как недоступный для одиночных ошибок соединения
            # load_balancer.mark_proxy_unavailable(proxy_port)
            
            try:
                self._send_error_response(
                    502, f"Tunnel connection failed: {str(e)}")
            except Exception as send_error:
                logger.debug(f"Could not send error response: {send_error}")
                pass  # Соединение могло быть уже закрыто
        finally:
            # proxy_socket закрывается в _tunnel_data
            pass

    def _create_socks_socket_from_session(self, session: requests.Session, target_host: str, target_port: int):
        import socks

        socks_proxy = session.proxies.get(
            'https') or session.proxies.get('http')
        if not socks_proxy:
            raise Exception("No proxy configuration in session")
        
        # Более безопасное извлечение хоста и порта из URL прокси
        try:
            parsed = urllib.parse.urlparse(socks_proxy)
            if parsed.scheme != 'socks5':
                raise Exception(f"Unsupported proxy scheme: {parsed.scheme}")
            
            proxy_host = parsed.hostname
            proxy_port = parsed.port or 1080
            
            if not proxy_host:
                raise Exception(f"Invalid proxy URL format: {socks_proxy}")
        except Exception as e:
            logger.error(f"Failed to parse SOCKS5 URL '{socks_proxy}': {e}")
            raise Exception(f"Invalid SOCKS5 proxy URL: {str(e)}")

        # Создаем новый SOCKS сокет для каждого соединения
        proxy_socket = socks.socksocket()
        proxy_socket.set_proxy(socks.SOCKS5, proxy_host, proxy_port)
        # Увеличенный таймаут для Tor соединений - 60 секунд вместо 30
        proxy_socket.settimeout(60)
        
        # Важно: включаем опцию повторного использования адреса
        proxy_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        logger.debug(f"Connecting to {target_host}:{target_port} via SOCKS5 proxy {proxy_host}:{proxy_port}")
        
        # Делаем только одну попытку соединения без ретраев
        try:
            proxy_socket.connect((target_host, target_port))
            logger.debug(f"Successfully connected to {target_host}:{target_port}")
            return proxy_socket
        except (socket.timeout, socket.error) as e:
            logger.error(f"Failed to connect to {target_host}:{target_port}: {e}")
            raise

    def _tunnel_data(self, client_socket, proxy_socket):
        try:
            # НЕ устанавливаем таймауты на клиентский сокет для HTTP сервера
            proxy_socket.settimeout(30)

            while True:
                try:
                    ready_sockets, _, error_sockets = select.select(
                        [client_socket, proxy_socket], [], [
                            client_socket, proxy_socket], 1.0
                    )

                    if error_sockets:
                        logger.warning("Error sockets detected in tunnel")
                        break

                    # Если нет готовых сокетов, продолжаем
                    if not ready_sockets:
                        continue

                    if client_socket in ready_sockets:
                        try:
                            data = client_socket.recv(8192)
                            if not data:
                                logger.debug("Client closed connection")
                                break
                            proxy_socket.sendall(data)
                        except socket.timeout:
                            continue
                        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
                            logger.debug(f"Client connection error: {e}")
                            break
                        except OSError as e:
                            if e.errno in (9, 107):  # Bad file descriptor, Transport endpoint is not connected
                                logger.debug(f"Client socket closed: {e}")
                                break
                            logger.error(f"Client socket OS error: {e}")
                            break
                        except Exception as e:
                            logger.error(f"Unexpected client socket error: {e}")
                            break

                    if proxy_socket in ready_sockets:
                        try:
                            data = proxy_socket.recv(8192)
                            if not data:
                                logger.debug("Proxy closed connection")
                                break
                            client_socket.sendall(data)
                        except socket.timeout:
                            continue
                        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError) as e:
                            logger.debug(f"Proxy connection error: {e}")
                            break
                        except OSError as e:
                            if e.errno in (9, 107):  # Bad file descriptor, Transport endpoint is not connected
                                logger.debug(f"Proxy socket closed: {e}")
                                break
                            logger.error(f"Proxy socket OS error: {e}")
                            break
                        except Exception as e:
                            logger.error(f"Unexpected proxy socket error: {e}")
                            break

                except select.error as e:
                    logger.error(f"Select error in tunnel: {e}")
                    break
                except Exception as e:
                    logger.error(f"Unexpected error in tunnel loop: {e}")
                    break

        except Exception as e:
            logger.error(f"Fatal error in tunnel_data: {e}")
        finally:
            # Закрываем только proxy_socket, client_socket управляется HTTP сервером
            try:
                if proxy_socket:
                    proxy_socket.close()
            except:
                pass

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
    def __init__(self, listen_port: int = 8080):
        self.listen_port = listen_port
        self.available_proxies: List[int] = []
        self.unavailable_proxies: List[int] = []
        self.proxy_sessions: Dict[int, requests.Session] = {}
        self.proxy_failure_counts: Dict[int, int] = {}  # Счетчик ошибок для каждого прокси
        self.current_index = 0
        self.lock = threading.Lock()
        self.server: Optional[ThreadingHTTPServer] = None
        self.server_thread: Optional[threading.Thread] = None
        self.stats_manager = StatisticsManager()
        self.last_health_check = 0
        self.health_check_interval = 30
        self.health_check_thread: Optional[threading.Thread] = None
        self.health_check_stop_event = threading.Event()

    def add_proxy(self, port: int):
        with self.lock:
            if port in self.proxy_sessions:
                return

            session = requests.Session()
            session.proxies = {
                'http': f'socks5://127.0.0.1:{port}',
                'https': f'socks5://127.0.0.1:{port}'
            }

            self.proxy_sessions[port] = session
            self.stats_manager.add_proxy(port)

            time.sleep(3)

            if self._test_proxy_connection(session, port):
                self.available_proxies.append(port)
                logger.info(f"Added available SOCKS5 proxy on port {port}")
            else:
                self.unavailable_proxies.append(port)
                logger.warning(
                    f"Added unavailable SOCKS5 proxy on port {port}")

    def remove_proxy(self, port: int):
        with self.lock:
            if port in self.available_proxies:
                self.available_proxies.remove(port)

            if port in self.unavailable_proxies:
                self.unavailable_proxies.remove(port)

            if port in self.proxy_sessions:
                try:
                    self.proxy_sessions[port].close()
                except Exception:
                    pass
                del self.proxy_sessions[port]

            self.stats_manager.remove_proxy(port)
            logger.info(f"Removed SOCKS5 proxy on port {port}")

    def _test_proxy_connection(self, session: requests.Session, port: int) -> bool:
        test_urls = [
            'http://httpbin.org/ip',
            'http://icanhazip.com',
            'http://ifconfig.me/ip'
        ]
        
        # Пробуем каждый URL только один раз, без ретраев
        for url in test_urls:
            try:
                # Один таймаут без увеличения
                response = session.get(url, timeout=30)
                if response.status_code == 200:
                    return True
            except Exception as e:
                logger.debug(f"Test connection to {url} via port {port} failed: {e}")
                continue

        return False

    def get_next_proxy(self) -> Optional[int]:
        with self.lock:
            if not self.available_proxies:
                return None

            # Используем случайный выбор вместо круговой очереди для лучшего распределения нагрузки
            proxy_port = random.choice(self.available_proxies)
            return proxy_port

    def get_proxy_session(self, port: int) -> Optional[requests.Session]:
        return self.proxy_sessions.get(port)

    def mark_proxy_unavailable(self, port: int, consecutive_failures: int = 1):
        """
        Помечает прокси как недоступный только после нескольких подряд идущих ошибок
        """
        with self.lock:
            if not hasattr(self, 'proxy_failure_counts'):
                self.proxy_failure_counts = {}
            
            if port not in self.proxy_failure_counts:
                self.proxy_failure_counts[port] = 0
            
            self.proxy_failure_counts[port] += consecutive_failures
            
            # Уменьшаем порог с 5 до 3 для более быстрого обнаружения неработающих прокси
            if self.proxy_failure_counts[port] >= 3:
                if port in self.available_proxies:
                    self.available_proxies.remove(port)
                    if port not in self.unavailable_proxies:
                        self.unavailable_proxies.append(port)
                    logger.warning(
                        f"Moved SOCKS5 proxy {port} to unavailable list after {self.proxy_failure_counts[port]} failures")

                if self.current_index >= len(self.available_proxies) and self.available_proxies:
                    self.current_index = 0
            else:
                logger.debug(f"SOCKS5 proxy {port} has {self.proxy_failure_counts[port]} failures, but still available")

    def mark_proxy_success(self, port: int):
        """
        Сбрасывает счетчик ошибок для прокси после успешного запроса
        """
        if hasattr(self, 'proxy_failure_counts') and port in self.proxy_failure_counts:
            self.proxy_failure_counts[port] = 0

    def start(self):
        if self.server_thread and self.server_thread.is_alive():
            logger.warning("HTTP Load balancer is already running")
            return

        try:
            self.server = LoadBalancerHTTPServer(
                ('0.0.0.0', self.listen_port), LoadBalancerHandler)
            self.server.load_balancer = self

            self.server_thread = threading.Thread(
                target=self.server.serve_forever, daemon=True)
            self.server_thread.start()

            self.health_check_stop_event.clear()
            self.health_check_thread = threading.Thread(
                target=self._background_health_checker, daemon=True)
            self.health_check_thread.start()

            logger.info(
                f"HTTP Load Balancer started on port {self.listen_port}")
        except Exception as e:
            logger.error(f"Failed to start HTTP load balancer: {e}")
            raise

    def stop(self):
        logger.info("Stopping HTTP Load Balancer...")

        if self.health_check_thread and self.health_check_thread.is_alive():
            self.health_check_stop_event.set()
            self.health_check_thread.join(timeout=10)

        if self.server:
            self.server.shutdown()
            self.server.server_close()

        if self.server_thread and self.server_thread.is_alive():
            self.server_thread.join(timeout=5)

        with self.lock:
            for port, session in self.proxy_sessions.items():
                try:
                    session.close()
                except Exception:
                    pass

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

    def _check_unavailable_proxies(self):
        """Проверяет недоступные прокси и восстанавливает работающие"""
        if not self.unavailable_proxies:
            return
            
        # Проверяем максимум 2 прокси за раз
        max_check = min(2, len(self.unavailable_proxies))
        proxies_to_check = self.unavailable_proxies[:max_check]
        
        for port in proxies_to_check:
            if self.health_check_stop_event.is_set():
                break
                
            session = self.proxy_sessions.get(port)
            if session and self._test_proxy_connection(session, port):
                self._move_proxy_to_available(port)
                logger.info(f"SOCKS5 proxy {port} is back online")

    def _check_available_proxies(self):
        """Проверяет случайный доступный прокси на работоспособность"""
        if not self.available_proxies:
            return
            
        # Выбираем случайный прокси для проверки
        random_proxy = random.choice(self.available_proxies)
        session = self.proxy_sessions.get(random_proxy)
        
        if session and not self._test_proxy_connection(session, random_proxy):
            self._move_proxy_to_unavailable(random_proxy)
            logger.warning(f"SOCKS5 proxy {random_proxy} became unavailable")

    def _move_proxy_to_available(self, port: int):
        """Перемещает прокси в список доступных"""
        with self.lock:
            if port in self.unavailable_proxies:
                self.unavailable_proxies.remove(port)
            if port not in self.available_proxies:
                self.available_proxies.append(port)

    def _move_proxy_to_unavailable(self, port: int):
        """Перемещает прокси в список недоступных"""
        with self.lock:
            if port in self.available_proxies:
                self.available_proxies.remove(port)
            if port not in self.unavailable_proxies:
                self.unavailable_proxies.append(port)

    def _background_health_checker(self):
        """Фоновый процесс проверки здоровья прокси"""
        while not self.health_check_stop_event.is_set():
            try:
                # Проверяем недоступные прокси на восстановление
                self._check_unavailable_proxies()
                
                # Проверяем доступные прокси на сбои
                if not self.health_check_stop_event.is_set():
                    self._check_available_proxies()
                    
                # Ждем до следующей проверки
                self.health_check_stop_event.wait(self.health_check_interval)
                
            except Exception as e:
                logger.error(f"Error in health checker: {e}")
                self.health_check_stop_event.wait(5)
