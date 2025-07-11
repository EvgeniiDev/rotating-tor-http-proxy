#!/usr/bin/env python3
import unittest
import subprocess
import time
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestBasicIntegration(unittest.TestCase):
    
    def setUp(self):
        self.process = None
    
    def tearDown(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
    
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


if __name__ == '__main__':
    unittest.main()
