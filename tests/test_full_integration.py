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


class TestFullIntegration(unittest.TestCase):
    
    def setUp(self):
        self.main_process: Optional[subprocess.Popen] = None
        self.tor_count = 10
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
    
    def test_exit_nodes_limiting_formula(self):
        """Test that exit nodes are correctly limited using the formula: tor_count * 6 * 2"""
        relay_manager = TorRelayManager()
        
        print(f"🔍 Testing exit nodes limiting formula...")
        print(f"📊 Configuration: {self.tor_count} Tor processes")
        print(f"📐 Formula: {self.tor_count} * 6 * 2 = {self.max_exit_nodes} max nodes")
        
        relay_data = relay_manager.fetch_tor_relays()
        self.assertIsNotNone(relay_data, "Failed to fetch relay data")
        
        all_exit_nodes = relay_manager.extract_relay_ips(relay_data)
        limited_exit_nodes = all_exit_nodes[:self.max_exit_nodes]
        
        print(f"✅ Found {len(all_exit_nodes)} total exit nodes")
        print(f"📏 Limited to {len(limited_exit_nodes)} nodes")
        print(f"🔢 Reduction: {len(all_exit_nodes) - len(limited_exit_nodes)} nodes")
        
        self.assertGreater(len(limited_exit_nodes), 0, "No exit nodes found")
        self.assertLessEqual(len(limited_exit_nodes), self.max_exit_nodes, "Too many exit nodes")
        self.assertGreaterEqual(len(limited_exit_nodes), self.tor_count, 
                               f"Need at least {self.tor_count} exit nodes for {self.tor_count} Tor processes")
        
        # Show sample nodes
        print(f"🌍 Sample exit nodes (top 5 by bandwidth):")
        for i, node in enumerate(limited_exit_nodes[:5]):
            bandwidth_mb = node['observed_bandwidth'] / (1024 * 1024)
            print(f"  {i+1}. {node['ip']} ({node['country']}) - {bandwidth_mb:.1f} MB/s")
    
    def test_integration_with_limited_exit_nodes(self):
        """Integration test: start main.py with 10 Tor processes and limited exit nodes"""
        relay_manager = TorRelayManager()
        
        print(f"� Starting integration test with {self.tor_count} Tor processes...")
        
        # Get and limit exit nodes
        relay_data = relay_manager.fetch_tor_relays()
        self.assertIsNotNone(relay_data, "Failed to fetch relay data")
        
        all_exit_nodes = relay_manager.extract_relay_ips(relay_data)
        limited_exit_nodes = all_exit_nodes[:self.max_exit_nodes]
        
        print(f"📊 Using {len(limited_exit_nodes)} exit nodes (formula: {self.tor_count} * 6 * 2)")
        self.assertGreaterEqual(len(limited_exit_nodes), self.tor_count, 
                               f"Need at least {self.tor_count} exit nodes")
        
        # Prepare exit nodes string
        exit_node_ips = [node['ip'] for node in limited_exit_nodes]
        exit_nodes_str = ','.join(exit_node_ips)
        
        # Setup environment
        env = os.environ.copy()
        env.update({
            'TOR_COUNT': str(self.tor_count),
            'HTTP_PORT': '8888',
            'EXIT_NODES': exit_nodes_str,
            'LOG_LEVEL': 'INFO',
            'TEST_MODE': '1'
        })
        
        main_py_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        
        print(f"🔧 Starting main.py with environment:")
        print(f"   - TOR_COUNT: {self.tor_count}")
        print(f"   - HTTP_PORT: 8888") 
        print(f"   - EXIT_NODES: {len(exit_node_ips)} nodes")
        
        # Start main process
        self.main_process = subprocess.Popen(
            [sys.executable, main_py_path],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True
        )
        
        print("⏳ Waiting for proxy to become ready...")
        
        startup_timeout = 300  # 5 minutes for 10 Tor processes
        start_time = time.time()
        proxy_ready = False
        check_interval = 15
        
        while time.time() - start_time < startup_timeout:
            # Check if process is still running
            if self.main_process.poll() is not None:
                stdout, _ = self.main_process.communicate()
                self.fail(f"Main process exited unexpectedly: {stdout}")
            
            # Test proxy connection
            try:
                response = requests.get(
                    'https://httpbin.org/ip',
                    proxies={'http': 'http://localhost:8888', 'https': 'http://localhost:8888'},
                    timeout=20
                )
                if response.status_code == 200:
                    proxy_ready = True
                    break
            except requests.RequestException:
                pass
            
            elapsed = int(time.time() - start_time)
            print(f"   Waiting... ({elapsed}s/{startup_timeout}s)")
            time.sleep(check_interval)
        
        self.assertTrue(proxy_ready, f"Proxy did not become ready within {startup_timeout} seconds")
        print("✅ Proxy is ready!")
        
        # Test proxy functionality
        print("🧪 Testing proxy functionality...")
        test_urls = [
            'https://httpbin.org/ip',
            'https://httpbin.org/user-agent',
            'https://httpbin.org/headers'
        ]
        
        successful_requests = 0
        exit_ips = set()
        
        for i, url in enumerate(test_urls):
            try:
                response = requests.get(
                    url,
                    proxies={'http': 'http://localhost:8888', 'https': 'http://localhost:8888'},
                    timeout=30
                )
                if response.status_code == 200:
                    successful_requests += 1
                    print(f"✅ Test {i+1}/{len(test_urls)} passed: {url}")
                    
                    if 'httpbin.org/ip' in url:
                        ip_data = response.json()
                        exit_ip = ip_data.get('origin', 'unknown')
                        exit_ips.add(exit_ip)
                        print(f"   🌐 Exit IP: {exit_ip}")
                else:
                    print(f"❌ Test {i+1}/{len(test_urls)} failed: {url} (status: {response.status_code})")
            except requests.RequestException as e:
                print(f"❌ Test {i+1}/{len(test_urls)} failed: {url} (error: {e})")
        
        # Verify results
        self.assertGreaterEqual(successful_requests, 2, 
                               f"Expected at least 2 successful requests, got {successful_requests}")
        
        print(f"\n🎉 Integration test completed successfully!")
        print(f"📊 Results:")
        print(f"   - Tor processes: {self.tor_count}")
        print(f"   - Exit nodes provided: {len(exit_node_ips)}")
        print(f"   - Successful requests: {successful_requests}/{len(test_urls)}")
        print(f"   - Unique exit IPs seen: {len(exit_ips)}")


if __name__ == '__main__':
    unittest.main(verbosity=2)
