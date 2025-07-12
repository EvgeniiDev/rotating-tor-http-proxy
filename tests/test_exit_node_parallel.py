#!/usr/bin/env python3

import unittest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import Mock, patch, MagicMock, mock_open
from exit_node_tester import ExitNodeChecker

class TestExitNodeParallel(unittest.TestCase):
    def setUp(self):
        self.mock_config_builder = Mock()
        self.checker = ExitNodeChecker(
            test_requests_count=2,
            required_success_count=1,
            timeout=5,
            config_builder=self.mock_config_builder,
            max_workers=3
        )

    @patch('exit_node_tester.TorInstance')
    def test_reconfigurable_exit_node_testing(self, mock_tor_instance_class):
        mock_instance = Mock()
        mock_instance.check_health.return_value = True
        mock_instance.get_proxies.return_value = {'http': 'socks5://127.0.0.1:9050'}
        mock_instance.reconfigure.return_value = True
        mock_instance.start.return_value = None
        mock_instance.stop.return_value = None
        mock_tor_instance_class.return_value = mock_instance
        
        with patch.object(self.checker, 'test_node', return_value=True):
            exit_nodes = ['1.1.1.1', '2.2.2.2', '3.3.3.3']
            result = self.checker.test_exit_nodes_parallel(exit_nodes, 2)
            
            self.assertEqual(len(result), 2)
            self.assertTrue(all(node in exit_nodes for node in result))

    @patch('exit_node_tester.TorInstance')
    def test_no_working_nodes(self, mock_tor_instance_class):
        mock_instance = Mock()
        mock_instance.check_health.return_value = False
        mock_tor_instance_class.return_value = mock_instance
        
        exit_nodes = ['1.1.1.1', '2.2.2.2']
        result = self.checker.test_exit_nodes_parallel(exit_nodes, 2)
        
        self.assertEqual(len(result), 0)

    def test_empty_exit_nodes(self):
        result = self.checker.test_exit_nodes_parallel([], 5)
        self.assertEqual(len(result), 0)

    def test_no_config_builder(self):
        checker_no_config = ExitNodeChecker()
        result = checker_no_config.test_exit_nodes_parallel(['1.1.1.1'], 1)
        self.assertEqual(len(result), 0)

if __name__ == '__main__':
    unittest.main()
