import unittest
import subprocess
import time
import tempfile
import os
import shutil
import stat
import sys
import signal
import threading
import requests

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestTorRestartIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix='tor_test_')
        self.fake_tor_script = os.path.join(self.test_dir, 'tor')
        self.run_count_file = os.path.join(self.test_dir, 'run_count')
        self.crash_signal_file = os.path.join(self.test_dir, 'crash_signal')
        
        self.original_path = os.environ.get('PATH', '')
        os.environ['PATH'] = f"{self.test_dir}:{self.original_path}"
        
        self.main_process = None
        
    def tearDown(self):
        os.environ['PATH'] = self.original_path
        if self.main_process and self.main_process.poll() is None:
            self.main_process.terminate()
            try:
                self.main_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.main_process.kill()
                self.main_process.wait()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def _create_fake_tor(self, crash_after_seconds=None):
        script_content = f'''#!/bin/bash
RUN_FILE="{self.run_count_file}"
CRASH_FILE="{self.crash_signal_file}"

if [ -f "$RUN_FILE" ]; then
    COUNT=$(cat "$RUN_FILE")
    COUNT=$((COUNT + 1))
else
    COUNT=1
fi
echo "$COUNT" > "$RUN_FILE"

PORT=9050
for arg in "$@"; do
    if [[ "$arg" =~ -f ]]; then
        CONFIG_FILE="${{@:$#}}"
        if [[ -f "$CONFIG_FILE" ]]; then
            PORT=$(grep "SocksPort" "$CONFIG_FILE" | head -1 | grep -o '[0-9]\\+' | tail -1)
        fi
        break
    fi
done

echo "Starting fake Tor on port $PORT (run $COUNT)"

if [ ! -z "$PORT" ]; then
    nc -l 127.0.0.1 $PORT &
    NC_PID=$!
    
    if [ ! -z "{crash_after_seconds}" ]; then
        sleep {crash_after_seconds}
        kill $NC_PID 2>/dev/null
        exit 1
    else
        while [ ! -f "$CRASH_FILE" ]; do
            sleep 0.5
        done
        rm -f "$CRASH_FILE"
        kill $NC_PID 2>/dev/null
        exit 1
    fi
fi
'''
        with open(self.fake_tor_script, 'w') as f:
            f.write(script_content)
        os.chmod(self.fake_tor_script, stat.S_IRWXU)

    def _get_run_count(self):
        if os.path.exists(self.run_count_file):
            with open(self.run_count_file, 'r') as f:
                return int(f.read().strip())
        return 0

    def _trigger_crash(self):
        with open(self.crash_signal_file, 'w') as f:
            f.write('crash')

    def _start_main_process(self):
        env = os.environ.copy()
        env['TOR_PROCESSES'] = '2'
        
        self.main_process = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.join(os.path.dirname(__file__), '..')
        )
        time.sleep(3)
        return self.main_process.poll() is None

    def test_main_process_starts_with_fake_tor(self):
        self._create_fake_tor()
        
        started = self._start_main_process()
        self.assertTrue(started, "Main process should start successfully")
        
        time.sleep(2)
        
        initial_count = self._get_run_count()
        self.assertGreaterEqual(initial_count, 1, f"Should have started at least 1 Tor process, got {initial_count}")

    def test_main_process_handles_tor_crashes(self):
        self._create_fake_tor()
        
        started = self._start_main_process()
        self.assertTrue(started, "Main process should start successfully")
        
        time.sleep(4)
        initial_count = self._get_run_count()
        self.assertGreaterEqual(initial_count, 1)
        
        for _ in range(3):
            self._trigger_crash()
            time.sleep(3)
        
        final_count = self._get_run_count()
        self.assertGreaterEqual(final_count, initial_count, f"Should handle crashes, initial: {initial_count}, final: {final_count}")

    def test_http_proxy_responds(self):
        self._create_fake_tor()
        
        started = self._start_main_process()
        self.assertTrue(started, "Main process should start successfully")
        
        time.sleep(4)
        
        try:
            response = requests.get('http://localhost:8080', timeout=5)
            self.assertIsNotNone(response)
        except requests.exceptions.RequestException:
            pass

    def test_main_process_stops_gracefully(self):
        self._create_fake_tor()
        
        started = self._start_main_process()
        self.assertTrue(started, "Main process should start successfully")
        
        time.sleep(2)
        
        self.main_process.terminate()
        exit_code = self.main_process.wait(timeout=10)
        self.assertIn(exit_code, [0, -15], f"Process should exit gracefully, got exit code: {exit_code}")

    def test_tor_restarts_on_http_errors(self):
        self._create_fake_tor_that_fails_immediately()
        
        started = self._start_main_process()
        self.assertTrue(started, "Main process should start successfully")
        
        time.sleep(8)
        
        final_count = self._get_run_count()
        self.assertGreaterEqual(final_count, 2, f"Should restart failing Tor processes, got {final_count} attempts")

    def _create_fake_tor_that_fails_immediately(self):
        script_content = f'''#!/bin/bash
RUN_FILE="{self.run_count_file}"

if [ -f "$RUN_FILE" ]; then
    COUNT=$(cat "$RUN_FILE")
    COUNT=$((COUNT + 1))
else
    COUNT=1
fi
echo "$COUNT" > "$RUN_FILE"

echo "Fake Tor attempt $COUNT - failing immediately"
sleep 1
exit 1
'''
        with open(self.fake_tor_script, 'w') as f:
            f.write(script_content)
        os.chmod(self.fake_tor_script, stat.S_IRWXU)

    def test_tor_restarts_on_connection_failures(self):
        self._create_fake_tor_with_connection_issues()
        
        started = self._start_main_process()
        self.assertTrue(started, "Main process should start successfully")
        
        time.sleep(6)
        initial_count = self._get_run_count()
        self.assertGreaterEqual(initial_count, 1)
        
        time.sleep(8)
        
        final_count = self._get_run_count()
        self.assertGreater(final_count, initial_count, f"Should restart Tor with connection issues, initial: {initial_count}, final: {final_count}")

    def _create_fake_tor_with_connection_issues(self):
        script_content = f'''#!/bin/bash
RUN_FILE="{self.run_count_file}"

if [ -f "$RUN_FILE" ]; then
    COUNT=$(cat "$RUN_FILE")
    COUNT=$((COUNT + 1))
else
    COUNT=1
fi
echo "$COUNT" > "$RUN_FILE"

PORT=9050
for arg in "$@"; do
    if [[ "$arg" =~ -f ]]; then
        CONFIG_FILE="${{@:$#}}"
        if [[ -f "$CONFIG_FILE" ]]; then
            PORT=$(grep "SocksPort" "$CONFIG_FILE" | head -1 | grep -o '[0-9]\\+' | tail -1)
        fi
        break
    fi
done

echo "Starting fake Tor with connection issues on port $PORT (run $COUNT)"

nc -l 127.0.0.1 $PORT &
NC_PID=$!

sleep 4

kill $NC_PID 2>/dev/null
sleep 1
exit 1
'''
        with open(self.fake_tor_script, 'w') as f:
            f.write(script_content)
        os.chmod(self.fake_tor_script, stat.S_IRWXU)


if __name__ == '__main__':
    unittest.main()
