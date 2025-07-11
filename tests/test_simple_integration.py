#!/usr/bin/env python3
import unittest
import subprocess
import time
import requests
import signal
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestSimpleIntegration(unittest.TestCase):
    
    def setUp(self):
        self.process = None
        self.proxy_port = 8081
        self.tor_count = 2
    
    def tearDown(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
    
    def test_simple_proxy_startup(self):
        script_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        
        env = os.environ.copy()
        env['TOR_COUNT'] = str(self.tor_count)
        env['PROXY_PORT'] = str(self.proxy_port)
        
        self.process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=os.path.dirname(script_path)
        )
        
        time.sleep(10)
        
        if self.process.poll() is not None:
            stdout, stderr = self.process.communicate()
            print(f"Process stdout: {stdout}")
            print(f"Process stderr: {stderr}")
            self.fail("Process exited early")
        
        try:
            response = requests.get(
                'http://httpbin.org/ip',
                proxies={'http': f'http://localhost:{self.proxy_port}'},
                timeout=5
            )
            
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertIn('origin', data)
            print(f"Successfully connected through proxy, IP: {data['origin']}")
            
        except requests.exceptions.RequestException as e:
            stdout, stderr = self.process.communicate()
            print(f"Request failed: {e}")
            print(f"Process stdout: {stdout}")
            print(f"Process stderr: {stderr}")
            self.fail(f"Failed to make request through proxy: {e}")


if __name__ == '__main__':
    unittest.main()
