#!/usr/bin/env python3
import unittest
import subprocess
import time
import requests
import signal
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestIntegration(unittest.TestCase):
    
    def setUp(self):
        self.process = None
        self.proxy_port = 8080
        self.tor_count = 10
    
    def tearDown(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
    
    def test_tor_proxy_integration(self):
        script_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        
        env = os.environ.copy()
        env['TOR_COUNT'] = str(self.tor_count)
        
        self.process = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=os.path.dirname(script_path)
        )
        
        max_wait_time = 60
        wait_interval = 2
        elapsed_time = 0
        
        while elapsed_time < max_wait_time:
            if self.process.poll() is not None:
                stdout, stderr = self.process.communicate()
                self.fail(f"Process exited early. Stdout: {stdout}, Stderr: {stderr}")
            
            try:
                response = requests.get(
                    'http://httpbin.org/ip',
                    proxies={'http': f'http://localhost:{self.proxy_port}'},
                    timeout=10
                )
                if response.status_code == 200:
                    break
            except requests.exceptions.RequestException:
                pass
            
            time.sleep(wait_interval)
            elapsed_time += wait_interval
        else:
            stdout, stderr = self.process.communicate()
            self.fail(f"Proxy did not become available within {max_wait_time} seconds. Stdout: {stdout}, Stderr: {stderr}")
        
        try:
            response = requests.get(
                'http://httpbin.org/ip',
                proxies={'http': f'http://localhost:{self.proxy_port}'},
                timeout=15
            )
            self.assertEqual(response.status_code, 200)
            
            data = response.json()
            self.assertIn('origin', data)
            self.assertTrue(len(data['origin']) > 0)
            
        except requests.exceptions.RequestException as e:
            self.fail(f"Failed to make request through proxy: {e}")


if __name__ == '__main__':
    unittest.main()
