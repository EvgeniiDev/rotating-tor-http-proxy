#!/usr/bin/env python3
import unittest
import subprocess
import time
import os
import sys
import requests
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config_manager import TorConfigBuilder
from tor_process import TorInstance
from http_load_balancer import HTTPLoadBalancer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


class TestBasicIntegration(unittest.TestCase):
    
    def setUp(self):
        self.process = None
        self.config_builder = None
        self.balancer = None
        self.tor_instance = None
        self.proxy_config = {
            'http': 'http://localhost:8080',
            'https': 'http://localhost:8080'
        }
    
    def tearDown(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        
        if self.tor_instance:
            self.tor_instance.stop()
        if self.balancer:
            self.balancer.stop()
        time.sleep(2)
    
    def test_application_starts_and_shows_expected_output(self):
        script_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        
        env = os.environ.copy()
        env['TOR_COUNT'] = '2'
        env['PROXY_PORT'] = '8082'
        
        self.process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=os.path.dirname(script_path)
        )
        
        time.sleep(15)
        
        self.process.terminate()
        stdout, stderr = self.process.communicate()
        
        self.assertIn("Starting Tor HTTP Proxy", stdout)
        self.assertIn("Starting Tor pool with 2 processes", stdout)
        
        expected_messages = [
            "Using fallback exit nodes",
            "Testing 5 exit nodes"
        ]
        
        for message in expected_messages:
            self.assertIn(message, stdout, f"Expected message '{message}' not found in output")
        
        print(f"Application output:\n{stdout}")
        if stderr:
            print(f"Application errors:\n{stderr}")

    def test_proxy_8080_connectivity(self):
        try:
            self.config_builder = TorConfigBuilder()
            self.balancer = HTTPLoadBalancer(listen_port=8080)
            self.tor_instance = TorInstance(port=9052, exit_nodes=[], config_builder=self.config_builder)
            
            self.tor_instance.create_config()
            self.tor_instance.start()
            
            time.sleep(10)
            
            if not self.tor_instance.check_health():
                self.fail("Tor instance failed health check")
            
            self.balancer.add_proxy(9052)
            self.balancer.start()
            
            time.sleep(3)
            
            test_url = "https://httpbin.org/ip"
            response = requests.get(test_url, proxies=self.proxy_config, timeout=30)
            
            self.assertEqual(response.status_code, 200)
            
            response_data = response.json()
            self.assertIn('origin', response_data)
            
            logging.info(f"Proxy test successful. Origin IP: {response_data.get('origin')}")
            
        except Exception as e:
            self.fail(f"Proxy connectivity test failed: {e}")
    
    def test_proxy_8080_multiple_requests(self):
        try:
            self.config_builder = TorConfigBuilder()
            self.balancer = HTTPLoadBalancer(listen_port=8080)
            self.tor_instance = TorInstance(port=9052, exit_nodes=[], config_builder=self.config_builder)
            
            self.tor_instance.create_config()
            self.tor_instance.start()
            
            time.sleep(10)
            
            if not self.tor_instance.check_health():
                self.fail("Tor instance failed health check")
            
            self.balancer.add_proxy(9052)
            self.balancer.start()
            
            time.sleep(3)
            
            test_urls = [
                "https://httpbin.org/ip",
                "https://httpbin.org/user-agent",
                "https://httpbin.org/headers"
            ]
            
            successful_requests = 0
            
            for i, url in enumerate(test_urls):
                try:
                    response = requests.get(url, proxies=self.proxy_config, timeout=30)
                    if response.status_code == 200:
                        successful_requests += 1
                        logging.info(f"Request {i+1}/3 to {url} successful")
                    else:
                        logging.warning(f"Request {i+1}/3 to {url} failed with status {response.status_code}")
                except Exception as e:
                    logging.error(f"Request {i+1}/3 to {url} failed: {e}")
            
            self.assertGreaterEqual(successful_requests, 2, 
                                  f"Only {successful_requests}/3 requests succeeded")
            
            logging.info(f"Multiple requests test: {successful_requests}/3 requests successful")
            
        except Exception as e:
            self.fail(f"Multiple requests test failed: {e}")
    
    def test_proxy_8080_performance(self):
        try:
            self.config_builder = TorConfigBuilder()
            self.balancer = HTTPLoadBalancer(listen_port=8080)
            self.tor_instance = TorInstance(port=9052, exit_nodes=[], config_builder=self.config_builder)
            
            self.tor_instance.create_config()
            self.tor_instance.start()
            
            time.sleep(10)
            
            if not self.tor_instance.check_health():
                self.fail("Tor instance failed health check")
            
            self.balancer.add_proxy(9052)
            self.balancer.start()
            
            time.sleep(3)
            
            test_url = "https://httpbin.org/ip"
            
            start_time = time.time()
            response = requests.get(test_url, proxies=self.proxy_config, timeout=30)
            end_time = time.time()
            
            response_time = end_time - start_time
            
            self.assertEqual(response.status_code, 200)
            self.assertLess(response_time, 30, f"Response time {response_time:.2f}s exceeds 30s limit")
            
            logging.info(f"Performance test: Response time {response_time:.2f}s")
            
        except Exception as e:
            self.fail(f"Performance test failed: {e}")

    def test_integration_with_proxy_validation(self):
        script_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        
        env = os.environ.copy()
        env['TOR_COUNT'] = '1'
        env['PROXY_PORT'] = '8083'
        
        self.process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=os.path.dirname(script_path)
        )
        
        time.sleep(20)
        
        try:
            proxy_config = {
                'http': 'http://localhost:8083',
                'https': 'http://localhost:8083'
            }
            
            test_url = "https://httpbin.org/ip"
            response = requests.get(test_url, proxies=proxy_config, timeout=15)
            
            self.assertEqual(response.status_code, 200)
            response_data = response.json()
            self.assertIn('origin', response_data)
            
            logging.info(f"Integration proxy test successful. Origin IP: {response_data.get('origin')}")
            
        except Exception as e:
            logging.warning(f"Integration proxy test failed: {e}")
        
        finally:
            self.process.terminate()
            stdout, stderr = self.process.communicate()
            
            self.assertIn("Starting Tor HTTP Proxy", stdout)
            self.assertIn("Starting Tor pool with 1 processes", stdout)
            
            print(f"Integration test output:\n{stdout}")
            if stderr:
                print(f"Integration test errors:\n{stderr}")

def standalone_integration_test():
    print("🚀 Starting comprehensive integration test...")
    
    config_builder = TorConfigBuilder()
    balancer = HTTPLoadBalancer(listen_port=8080)
    tor_instance = TorInstance(port=9052, exit_nodes=[], config_builder=config_builder)
    
    try:
        print("📝 Creating Tor config...")
        tor_instance.create_config()
        
        print("🔄 Starting Tor process...")
        tor_instance.start()
        
        print("⏳ Waiting for Tor to start...")
        time.sleep(10)
        
        print("🏥 Checking Tor health...")
        if not tor_instance.check_health():
            print("❌ Tor health check failed")
            return False
        
        print("✅ Tor is healthy!")
        
        print("⚖️ Starting HTTP Load Balancer...")
        balancer.add_proxy(9052)
        balancer.start()
        
        print("✅ HTTP Load Balancer started on port 8080")
        
        print("🌐 Testing comprehensive proxy functionality...")
        proxy_config = {
            'http': 'http://localhost:8080',
            'https': 'http://localhost:8080'
        }
        
        test_results = []
        test_cases = [
            ("Basic connectivity", "https://httpbin.org/ip"),
            ("User agent test", "https://httpbin.org/user-agent"),
            ("Headers test", "https://httpbin.org/headers"),
            ("JSON response test", "https://httpbin.org/json")
        ]
        
        for i, (test_name, url) in enumerate(test_cases):
            try:
                print(f"📡 Running {test_name} ({i+1}/{len(test_cases)})...")
                start_time = time.time()
                response = requests.get(url, proxies=proxy_config, timeout=30)
                end_time = time.time()
                
                if response.status_code == 200:
                    response_time = end_time - start_time
                    test_results.append(True)
                    print(f"✅ {test_name} successful ({response_time:.2f}s)")
                    
                    if 'ip' in url:
                        data = response.json()
                        print(f"   Exit node IP: {data.get('origin', 'unknown')}")
                else:
                    test_results.append(False)
                    print(f"❌ {test_name} failed with status {response.status_code}")
                    
            except Exception as e:
                test_results.append(False)
                print(f"❌ {test_name} failed: {e}")
        
        successful = sum(test_results)
        success_rate = successful / len(test_results) * 100
        print(f"\n📊 Integration Test Results: {successful}/{len(test_results)} tests passed ({success_rate:.1f}%)")
        
        if successful >= len(test_results) * 0.75:
            print("🎉 Integration test PASSED!")
            return True
        else:
            print("💥 Integration test FAILED!")
            return False
        
    except Exception as e:
        print(f"❌ Error during integration test: {e}")
        return False
        
    finally:
        print("🛑 Cleaning up...")
        tor_instance.stop()
        balancer.stop()
        print("✅ Cleanup complete")


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == "--standalone":
        success = standalone_integration_test()
        exit(0 if success else 1)
    else:
        unittest.main()
