#!/usr/bin/env python3
import unittest
import subprocess
import time
import requests
import signal
import os
import sys
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tor_relay_manager import TorRelayManager


class TestIntegrationSimple(unittest.TestCase):
    
    def setUp(self):
        self.main_process: Optional[subprocess.Popen] = None
        self.tor_count = 3
        self.max_exit_nodes = self.tor_count * 6 * 2
        
    def tearDown(self):
        if self.main_process:
            try:
                self.main_process.send_signal(signal.SIGTERM)
                self.main_process.wait(timeout=10)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    self.main_process.kill()
                    self.main_process.wait(timeout=5)
                except ProcessLookupError:
                    pass
            finally:
                self.main_process = None
        
        subprocess.run(['timeout', '5s', 'pkill', '-f', 'tor'], capture_output=True)
        time.sleep(2)
    
    def test_integration_with_real_exit_nodes(self):
        relay_manager = TorRelayManager()
        
        print(f"🔍 Fetching exit nodes for {self.tor_count} Tor processes...")
        relay_data = relay_manager.fetch_tor_relays()
        self.assertIsNotNone(relay_data, "Failed to fetch relay data")
        
        all_exit_nodes = relay_manager.extract_relay_ips(relay_data)
        limited_exit_nodes = all_exit_nodes[:self.max_exit_nodes]
        
        print(f"✅ Using {len(limited_exit_nodes)} exit nodes (limit: {self.max_exit_nodes})")
        self.assertGreater(len(limited_exit_nodes), self.tor_count, 
                          f"Need at least {self.tor_count} exit nodes for {self.tor_count} Tor processes")
        
        exit_node_ips = [node['ip'] for node in limited_exit_nodes]
        exit_nodes_str = ','.join(exit_node_ips)
        
        print(f"🚀 Starting main.py with {self.tor_count} Tor processes...")
        
        env = os.environ.copy()
        env.update({
            'TOR_COUNT': str(self.tor_count),
            'HTTP_PORT': '8889',
            'EXIT_NODES': exit_nodes_str,
            'LOG_LEVEL': 'INFO',
            'TEST_MODE': '1'
        })
        
        main_py_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        
        self.main_process = subprocess.Popen(
            [sys.executable, main_py_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        print("⏳ Waiting for application to start...")
        
        startup_timeout = 180
        start_time = time.time()
        proxy_ready = False
        
        while time.time() - start_time < startup_timeout:
            if self.main_process.poll() is not None:
                stdout, _ = self.main_process.communicate()
                self.fail(f"Main process exited unexpectedly: {stdout}")
            
            try:
                response = requests.get(
                    'https://httpbin.org/ip',
                    proxies={'http': 'http://localhost:8889', 'https': 'http://localhost:8889'},
                    timeout=15
                )
                if response.status_code == 200:
                    proxy_ready = True
                    break
            except requests.RequestException:
                pass
            
            print(".", end="", flush=True)
            time.sleep(10)
        
        self.assertTrue(proxy_ready, "Proxy did not become ready within timeout")
        print("\n✅ Proxy is ready!")
        
        print("🧪 Testing proxy functionality...")
        test_urls = [
            'https://httpbin.org/ip',
            'https://httpbin.org/user-agent'
        ]
        
        successful_requests = 0
        for i, url in enumerate(test_urls):
            try:
                response = requests.get(
                    url,
                    proxies={'http': 'http://localhost:8889', 'https': 'http://localhost:8889'},
                    timeout=30
                )
                if response.status_code == 200:
                    successful_requests += 1
                    print(f"✅ Test {i+1}/{len(test_urls)} passed: {url}")
                    if 'httpbin.org/ip' in url:
                        ip_data = response.json()
                        print(f"   🌐 Exit IP: {ip_data.get('origin', 'unknown')}")
                else:
                    print(f"❌ Test {i+1}/{len(test_urls)} failed: {url} (status: {response.status_code})")
            except requests.RequestException as e:
                print(f"❌ Test {i+1}/{len(test_urls)} failed: {url} (error: {e})")
        
        self.assertGreaterEqual(successful_requests, 1, 
                               f"Expected at least 1 successful request, got {successful_requests}")
        
        print(f"🎉 Integration test completed! {successful_requests}/{len(test_urls)} requests successful")


if __name__ == '__main__':
    unittest.main(verbosity=2)
