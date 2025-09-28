#!/usr/bin/env python3
"""
Unit tests for TorParallelRunner class
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tor_parallel_runner import TorParallelRunner  # type: ignore


class TorParallelRunnerTests(unittest.TestCase):
    def test_start_many_returns_successful_ports(self):
        runner = TorParallelRunner(config_builder=None, max_workers=3)
        ports = [11000, 11001, 11002]
        exit_nodes = [[], [], []]

        self.addCleanup(lambda: runner.shutdown())

        def fake_start_instance(self, port, nodes):
            with self._lock:
                if port == ports[1]:
                    self.instances[port] = None
                    return False
                self.instances[port] = SimpleNamespace(is_running=True, stop=lambda: None)
                return True

        with patch.object(TorParallelRunner, "_start_instance", new=fake_start_instance):
            started_ports = runner.start_many(ports, exit_nodes)

        self.assertEqual(started_ports, [ports[0], ports[2]])
        self.assertEqual(sorted(runner.instances.keys()), [ports[0], ports[2]])

    def test_start_many_stops_when_shutting_down(self):
        runner = TorParallelRunner(config_builder=None, max_workers=2)
        runner._shutdown_event.set()
        self.addCleanup(lambda: runner.shutdown())

        result = runner.start_many([12000], [[]])

        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()