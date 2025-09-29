#!/usr/bin/env python3
import argparse
import contextlib
import os
import random
import socket
import ssl
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPResponse, IncompleteRead, RemoteDisconnected
from urllib.parse import urlparse

import socks

class ProxyTester:
    def __init__(self, proxy_host="127.0.0.1", proxy_port=8080, total_requests=10, delay=5.0, threads=1):
        self.proxy_host = proxy_host
        self.proxy_port = proxy_port
        self.proxy_username = os.getenv("PROXY_USERNAME")
        self.proxy_password = os.getenv("PROXY_PASSWORD")
        self.proxy_url = self._build_proxy_url(include_auth=True)
        self.proxy_display = self._build_proxy_url(include_auth=False)
        self.total_requests = total_requests
        self.delay = delay
        self.threads = threads
        self.results = []
        self.response_codes = defaultdict(int)
        self.request_timestamps = []
        self.success_timestamps = []
        self.lock = threading.Lock()
        self.exception_types = defaultdict(int)
        
        self.target_urls = [
            "https://steamcommunity.com/market/listings/730/AK-47%20|%20Redline%20(Field-Tested)",
            "https://steamcommunity.com/market/listings/730/AWP%20|%20Dragon%20Lore%20(Factory%20New)",
            "https://steamcommunity.com/market/listings/730/M4A4%20|%20Howl%20(Field-Tested)",
            "https://steamcommunity.com/market/listings/730/Karambit%20|%20Fade%20(Factory%20New)",
            "https://steamcommunity.com/market/listings/730/Glock-18%20|%20Water%20Elemental%20(Factory%20New)",
            "https://steamcommunity.com/market/listings/730/StatTrak%E2%84%A2%20AK-47%20|%20Vulcan%20(Factory%20New)",
            "https://steamcommunity.com/market/listings/730/M4A1-S%20|%20Hyper%20Beast%20(Factory%20New)",
            "https://steamcommunity.com/market/listings/730/USP-S%20|%20Kill%20Confirmed%20(Factory%20New)",
            "https://steamcommunity.com/market/listings/730/Operation%20Hydra%20Case",
            "https://steamcommunity.com/market/listings/730/Chroma%203%20Case"
        ]

    def _build_proxy_url(self, include_auth=True):
        prefix = "http://"
        auth_segment = ""
        if include_auth and self.proxy_username:
            if self.proxy_password:
                auth_segment = f"{self.proxy_username}:{self.proxy_password}@"
            else:
                auth_segment = f"{self.proxy_username}@"
        return f"{prefix}{auth_segment}{self.proxy_host}:{self.proxy_port}"

    def clear_screen(self):
        os.system('clear' if os.name == 'posix' else 'cls')

    def print_dynamic_stats(self, current_request, total_requests, elapsed_time):
        with self.lock:
            total_completed = len(self.results)
            current_200 = self.response_codes.get(200, 0)
            current_429 = self.response_codes.get(429, 0)
            connection_errors = self.response_codes.get('CONNECTION_ERROR', 0)
            proxy_errors = self.response_codes.get('PROXY_ERROR', 0) 
            timeouts = self.response_codes.get('TIMEOUT', 0)
            decode_errors = self.response_codes.get('DECODE_ERROR', 0)
            other_errors = self.response_codes.get('OTHER_ERROR', 0)
            chunked_errors = self.response_codes.get('CHUNKED_ENCODING_ERROR', 0)
            exception_snapshot = dict(self.exception_types)
            current_rpm = self.calculate_rpm(self.request_timestamps) if len(self.request_timestamps) > 1 else 0
            success_rpm = self.calculate_rpm(self.success_timestamps) if len(self.success_timestamps) > 1 else 0
            
        success_pct = (current_200 / total_completed * 100) if total_completed > 0 else 0
        rate_limit_pct = (current_429 / total_completed * 100) if total_completed > 0 else 0
        
        progress = (current_request / total_requests) * 100
        bar_length = 40
        filled_length = int(bar_length * progress / 100)
        bar = '█' * filled_length + '░' * (bar_length - filled_length)
        
        self.clear_screen()
        print("=" * 90)
        print(f"🚀 STEAM MARKET PROXY TESTER - LIVE STATISTICS")
        print("=" * 90)
        print(f"📊 Progress: [{bar}] {progress:.1f}% ({current_request}/{total_requests})")
        print(f"⏱️  Elapsed Time: {elapsed_time:.1f}s | Avg per request: {elapsed_time/current_request:.1f}s")
        print(f"🔀 Threads: {self.threads} | Active requests: {current_request - total_completed}")
        print("-" * 90)
        
        print("📈 REAL-TIME STATISTICS:")
        print(f"✅ Success (200 OK):     {current_200:>6} ({success_pct:>5.1f}%)")
        print(f"⚠️  Rate Limited (429):  {current_429:>6} ({rate_limit_pct:>5.1f}%)")
        print(f"🔌 Connection Errors:    {connection_errors:>6} ({connection_errors/total_completed*100:>5.1f}%)" if total_completed > 0 else "🔌 Connection Errors:         0 (  0.0%)")
        print(f"🔀 Proxy Errors:        {proxy_errors:>6} ({proxy_errors/total_completed*100:>5.1f}%)" if total_completed > 0 else "🔀 Proxy Errors:             0 (  0.0%)")
        print(f"⏰ Timeouts:            {timeouts:>6} ({timeouts/total_completed*100:>5.1f}%)" if total_completed > 0 else "⏰ Timeouts:                 0 (  0.0%)")
        print(f"📦 Decode Errors:       {decode_errors:>6} ({decode_errors/total_completed*100:>5.1f}%)" if total_completed > 0 else "📦 Decode Errors:            0 (  0.0%)")
        print(f"💥 Other Errors:        {other_errors:>6} ({other_errors/total_completed*100:>5.1f}%)" if total_completed > 0 else "💥 Other Errors:             0 (  0.0%)")
        print(f"📡 Chunk Errors:       {chunked_errors:>6} ({chunked_errors/total_completed*100:>5.1f}%)" if total_completed > 0 else "📡 Chunk Errors:             0 (  0.0%)")
        if exception_snapshot:
            formatted = ', '.join(f"{k}: {v}" for k, v in sorted(exception_snapshot.items()))
            print(f"🔥 Exception Types:       {formatted}")
        
        print("-" * 90)
        print("🚀 RPM METRICS:")
        print(f"📊 Total RPM:            {current_rpm:>6.1f} requests/minute")
        print(f"✅ Success RPM (200):    {success_rpm:>6.1f} requests/minute")
        
        print("-" * 90)
        if len(self.results) > 0:
            print("-" * 90)
            print("📋 LAST 5 REQUESTS:")
            for i, result in enumerate(self.results[-5:], 1):
                status = result.get('status_code', 'ERROR')
                response_time = result.get('response_time', 0)
                if status == 200:
                    print(f"  {len(self.results)-5+i:>2}. ✅ HTTP {status} - {response_time:.2f}s")
                elif status == 429:
                    print(f"  {len(self.results)-5+i:>2}. ⚠️  HTTP {status} - {response_time:.2f}s")
                elif status is None:
                    error_type = result.get('result_type', 'unknown')
                    detail = result.get('exception_type') or result.get('error', 'unknown')
                    if error_type == 'decode_error':
                        print(f"  {len(self.results)-5+i:>2}. 📦 DECODE ERROR")
                    elif error_type == 'chunked_encoding_error':
                        print(f"  {len(self.results)-5+i:>2}. 📡 CHUNK ERROR {detail}")
                    elif error_type == 'exception':
                        print(f"  {len(self.results)-5+i:>2}. ❌ EXCEPTION {detail}")
                    else:
                        print(f"  {len(self.results)-5+i:>2}. ❌ {error_type.upper()} {detail}")
                else:
                    print(f"  {len(self.results)-5+i:>2}. ❓ HTTP {status} - {response_time:.2f}s")
        
        print("=" * 90)

    def _open_proxy_socket(self, host, port, timeout):
        sock = socks.socksocket()
        sock.set_proxy(
            socks.HTTP,
            addr=self.proxy_host,
            port=self.proxy_port,
            username=self.proxy_username,
            password=self.proxy_password,
            rdns=True,
        )
        sock.settimeout(timeout)
        sock.connect((host, port))
        return sock

    def _perform_http_request(self, url, headers, timeout):
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            raise ValueError(f"Unsupported URL: {url}")

        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path or "/"
        if parsed.params:
            path += f";{parsed.params}"
        if parsed.query:
            path += f"?{parsed.query}"

        request_headers = dict(headers)
        host_header = host
        if (parsed.scheme == "http" and port != 80) or (parsed.scheme == "https" and port != 443):
            host_header = f"{host}:{port}"
        request_headers["Host"] = host_header
        request_headers["Connection"] = "close"
        request_headers.setdefault("Accept-Encoding", "identity")

        request_lines = [f"GET {path} HTTP/1.1"]
        request_lines.extend(f"{key}: {value}" for key, value in request_headers.items())
        request_bytes = ("\r\n".join(request_lines) + "\r\n\r\n").encode("utf-8")

        start_time = time.time()
        sock = None
        wrapped_sock = None
        response = None
        try:
            sock = self._open_proxy_socket(host, port, timeout)
            wrapped_sock = sock
            if parsed.scheme == "https":
                context = ssl.create_default_context()
                context.check_hostname = False  # allow testing against proxies with self-signed certs
                context.verify_mode = ssl.CERT_NONE
                wrapped_sock = context.wrap_socket(sock, server_hostname=host)

            wrapped_sock.sendall(request_bytes)

            response = HTTPResponse(wrapped_sock)
            response.begin()
            body = response.read()
            status = response.status
            headers_map = {key: value for key, value in response.getheaders()}
            elapsed = time.time() - start_time

            # Ensure body is decoded only if it is a byte object
            if isinstance(body, bytes):
                body = body.decode('utf-8', errors='replace')

            return status, headers_map, body, elapsed
        finally:
            if response is not None:
                with contextlib.suppress(Exception):
                    response.close()
            if wrapped_sock is not None:
                with contextlib.suppress(Exception):
                    wrapped_sock.close()
            if sock is not None and sock is not wrapped_sock:
                with contextlib.suppress(Exception):
                    sock.close()

    def make_request(self, request_id, url):
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Cache-Control": "max-age=0"
        }

        try:
            status_code, response_headers, body, elapsed = self._perform_http_request(url, headers, timeout=60.0)
            content_length = len(body)
            content_encoding = (
                response_headers.get('Content-Encoding')
                or response_headers.get('content-encoding')
                or 'none'
            )

            item = {
                'request_id': request_id,
                'status_code': status_code,
                'response_time': round(elapsed, 3),
                'proxy_port': str(self.proxy_port),
                'timestamp': int(time.time()),
                'url': url,
                'content_length': content_length,
                'content_encoding': content_encoding
            }

            with self.lock:
                self.response_codes[status_code] += 1
                current_time = time.time()
                self.request_timestamps.append(current_time)

                if status_code == 200:
                    item['result_type'] = 'success'
                    self.success_timestamps.append(current_time)
                elif status_code == 429:
                    item['result_type'] = 'rate_limited'
                else:
                    item['result_type'] = 'http_error'
                    item['error'] = f'HTTP {status_code}'

            return item

        except (socks.ProxyError, socks.GeneralProxyError, socks.SOCKS5Error, socks.SOCKS4Error) as e:
            with self.lock:
                self.response_codes['PROXY_ERROR'] += 1
                self.request_timestamps.append(time.time())
                self.exception_types[type(e).__name__] += 1

            return {
                'request_id': request_id,
                'status_code': None,
                'response_time': None,
                'proxy_port': str(self.proxy_port),
                'result_type': 'proxy_error',
                'error': str(e)[:100],
                'exception_type': type(e).__name__,
                'timestamp': int(time.time()),
                'url': url
            }

        except socket.timeout as e:
            with self.lock:
                self.response_codes['TIMEOUT'] += 1
                self.request_timestamps.append(time.time())
                self.exception_types[type(e).__name__] += 1

            return {
                'request_id': request_id,
                'status_code': None,
                'response_time': None,
                'proxy_port': str(self.proxy_port),
                'result_type': 'timeout',
                'error': 'Request timeout',
                'exception_type': type(e).__name__,
                'timestamp': int(time.time()),
                'url': url
            }

        except (socket.error, RemoteDisconnected) as e:
            with self.lock:
                self.response_codes['CONNECTION_ERROR'] += 1
                self.request_timestamps.append(time.time())
                self.exception_types[type(e).__name__] += 1

            return {
                'request_id': request_id,
                'status_code': None,
                'response_time': None,
                'proxy_port': str(self.proxy_port),
                'result_type': 'connection_error',
                'error': str(e)[:100],
                'exception_type': type(e).__name__,
                'timestamp': int(time.time()),
                'url': url
            }

        except IncompleteRead as e:
            with self.lock:
                self.response_codes['CHUNKED_ENCODING_ERROR'] += 1
                self.request_timestamps.append(time.time())
                self.exception_types[type(e).__name__] += 1

            return {
                'request_id': request_id,
                'status_code': None,
                'response_time': None,
                'proxy_port': str(self.proxy_port),
                'result_type': 'chunked_encoding_error',
                'error': 'Chunked transfer terminated',
                'exception_type': type(e).__name__,
                'timestamp': int(time.time()),
                'url': url
            }

        except ssl.SSLError as e:
            with self.lock:
                self.response_codes['OTHER_ERROR'] += 1
                self.request_timestamps.append(time.time())
                self.exception_types[type(e).__name__] += 1

            return {
                'request_id': request_id,
                'status_code': None,
                'response_time': None,
                'proxy_port': str(self.proxy_port),
                'result_type': 'exception',
                'error': str(e)[:100],
                'exception_type': type(e).__name__,
                'timestamp': int(time.time()),
                'url': url
            }

        except Exception as e:
            with self.lock:
                self.response_codes['OTHER_ERROR'] += 1
                self.request_timestamps.append(time.time())
                self.exception_types[type(e).__name__] += 1

            return {
                'request_id': request_id,
                'status_code': None,
                'response_time': None,
                'proxy_port': str(self.proxy_port),
                'result_type': 'exception',
                'error': str(e)[:100],
                'exception_type': type(e).__name__,
                'timestamp': int(time.time()),
                'url': url
            }

    def run_test(self):
        self.clear_screen()
        print("=" * 90)
        print(f"🚀 STEAM MARKET PROXY TESTER {'(MULTI-THREADED)' if self.threads > 1 else '(SEQUENTIAL)'}")
        print("=" * 90)
        print(f"Proxy: {self.proxy_display}")
        print(f"Total requests: {self.total_requests}")
        print(f"Delay between requests: {self.delay}s")
        print(f"Threads: {self.threads}")
        print(f"Target URLs: {len(self.target_urls)} Steam market listing URLs")
        print("=" * 90)
        
        start_time = time.time()
        
        if self.threads == 1:
            for i in range(1, self.total_requests + 1):
                url = random.choice(self.target_urls)
                item = self.make_request(i, url)
                with self.lock:
                    self.results.append(item)
                
                elapsed = time.time() - start_time
                self.print_dynamic_stats(i, self.total_requests, elapsed)
                
                if i < self.total_requests:
                    for remaining in range(int(self.delay), 0, -1):
                        print(f"\r⏱️  Next request in: {remaining}s", end="", flush=True)
                        time.sleep(1)
                    print(f"\r⏱️  Next request in: 0s", flush=True)
        else:
            with ThreadPoolExecutor(max_workers=self.threads) as executor:
                futures = []
                
                for i in range(1, self.total_requests + 1):
                    url = random.choice(self.target_urls)
                    future = executor.submit(self.make_request, i, url)
                    futures.append(future)
                
                completed = 0
                for future in as_completed(futures):
                    result = future.result()
                    with self.lock:
                        self.results.append(result)
                        completed += 1
                    
                    elapsed = time.time() - start_time
                    self.print_dynamic_stats(completed, self.total_requests, elapsed)
        
        elapsed = time.time() - start_time
        print("\n" + "🎯 Test completed! Generating final report...")
        time.sleep(2)
        self.show_final_results(elapsed)

    def calculate_rpm(self, timestamps, duration_minutes=2):
        if len(timestamps) <= 1:
            return 0
        
        current_time = time.time()
        cutoff_time = current_time - (duration_minutes * 60)
        
        recent_requests = [t for t in timestamps if t >= cutoff_time]
        
        if len(recent_requests) <= 1:
            return 0
            
        actual_duration = (timestamps[-1] - timestamps[0]) / 60
        rpm = len(recent_requests) / actual_duration if actual_duration > 0 else 0
        
        # Clean up old timestamps to prevent memory leak
        timestamps[:] = recent_requests
        
        return rpm

    def show_final_results(self, elapsed):
        self.clear_screen()
        total_requests = len(self.results)
        
        code_200_count = self.response_codes.get(200, 0)
        code_429_count = self.response_codes.get(429, 0)
        chunked_errors = self.response_codes.get('CHUNKED_ENCODING_ERROR', 0)
        
        success_percentage = (code_200_count / total_requests * 100) if total_requests > 0 else 0
        rate_limit_percentage = (code_429_count / total_requests * 100) if total_requests > 0 else 0
        chunked_percentage = (chunked_errors / total_requests * 100) if total_requests > 0 else 0
        
        total_rpm = self.calculate_rpm(self.request_timestamps) if len(self.request_timestamps) > 1 else 0
        success_rpm = self.calculate_rpm(self.success_timestamps) if len(self.success_timestamps) > 1 else 0
        
        print("=" * 90)
        print("🎯 FINAL RESULTS - TEST COMPLETED")
        print("=" * 90)
        print(f"⏱️  Total time: {elapsed:.1f}s")
        print(f"📊 Total requests: {total_requests}")
        print(f"🧵 Threads used: {self.threads}")
        print("-" * 90)
        
        print("📈 STATUS CODE BREAKDOWN:")
        print(f"✅ HTTP 200 (Success):  {code_200_count:>6} ({success_percentage:>5.1f}%)")
        print(f"⚠️  HTTP 429 (Rate Limit): {code_429_count:>6} ({rate_limit_percentage:>5.1f}%)")
        print(f"📡 Chunk Errors:        {chunked_errors:>6} ({chunked_percentage:>5.1f}%)")
        
        for code, count in sorted(self.response_codes.items()):
            if isinstance(code, int) and code not in [200, 429]:
                percentage = (count / total_requests * 100) if total_requests > 0 else 0
                print(f"📊 HTTP {code}:           {count:>6} ({percentage:>5.1f}%)")

        if self.exception_types:
            print("-" * 90)
            print("🔥 EXCEPTION TYPES:")
            for name, count in sorted(self.exception_types.items()):
                print(f"  {name}: {count}")
                
        print("-" * 90)
        if total_requests > 0:
            successful_requests = [r for r in self.results if r.get('response_time') is not None and r.get('status_code') == 200]
            if successful_requests:
                avg_response_time = sum(r['response_time'] for r in successful_requests) / len(successful_requests)
                total_bytes = sum(r.get('content_length', 0) for r in successful_requests)
                print(f"⏱️ Average response time: {avg_response_time:.3f}s")
                print(f"📦 Total data received: {total_bytes:,} bytes")
        print("-" * 90)
        print("🚀 PERFORMANCE METRICS:")
        print(f"📊 Total RPM:            {total_rpm:>6.1f} requests/minute")
        print(f"✅ Success RPM (200):    {success_rpm:>6.1f} requests/minute")
        print("=" * 90)

def main():
    parser = argparse.ArgumentParser(description='Steam Market Proxy Tester with Multi-threading Support')
    parser.add_argument('--host', default='127.0.0.1', 
                       help='Proxy host (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=8080, 
                       help='Proxy port (default: 8080)')
    parser.add_argument('--requests', type=int, default=10, 
                       help='Total number of requests (default: 10)')
    parser.add_argument('--delay', type=float, default=5.0,
                       help='Delay between requests in seconds for sequential mode (default: 5.0)')
    parser.add_argument('--threads', type=int, default=1,
                       help='Number of concurrent threads (default: 1)')
    
    args = parser.parse_args()
    
    if args.requests <= 0:
        print("❌ Number of requests must be positive")
        sys.exit(1)
        
    if args.delay < 0:
        print("❌ Delay must be non-negative")
        sys.exit(1)
    
    if args.threads <= 0:
        print("❌ Number of threads must be positive")
        sys.exit(1)
    
    tester = ProxyTester(
        proxy_host=args.host,
        proxy_port=args.port,
        total_requests=args.requests,
        delay=args.delay,
        threads=args.threads
    )
    
    try:
        tester.run_test()
    except KeyboardInterrupt:
        print("\n🛑 Test stopped by user")
    except Exception as e:
        print(f"❌ Test failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()