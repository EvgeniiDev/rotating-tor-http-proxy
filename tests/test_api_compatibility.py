#!/usr/bin/env python3

import unittest
import sys
import os
import inspect
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from exit_node_tester import ExitNodeChecker
from tor_pool_manager import TorBalancerManager
from config_manager import TorConfigBuilder


class TestAPICompatibility(unittest.TestCase):
    
    def setUp(self):
        self.config_builder = Mock(spec=TorConfigBuilder)
        self.checker = ExitNodeChecker(config_builder=self.config_builder)
        
    def test_exit_node_checker_has_required_methods(self):
        required_methods = [
            'test_exit_nodes_parallel',
            'test_node',
            'test_nodes'
        ]
        
        for method_name in required_methods:
            with self.subTest(method=method_name):
                self.assertTrue(
                    hasattr(self.checker, method_name),
                    f"ExitNodeChecker missing required method: {method_name}"
                )
                self.assertTrue(
                    callable(getattr(self.checker, method_name)),
                    f"ExitNodeChecker.{method_name} is not callable"
                )
    
    def test_exit_node_checker_method_signatures(self):
        test_exit_nodes_parallel = getattr(self.checker, 'test_exit_nodes_parallel')
        sig = inspect.signature(test_exit_nodes_parallel)
        
        expected_params = ['exit_nodes', 'required_count']
        actual_params = list(sig.parameters.keys())
        
        for param in expected_params:
            self.assertIn(param, actual_params, 
                         f"test_exit_nodes_parallel missing parameter: {param}")
    
    @patch('http_load_balancer.HTTPLoadBalancer')
    @patch('tor_parallel_runner.TorParallelRunner')
    def test_tor_pool_manager_uses_correct_checker_methods(self, mock_runner, mock_balancer):
        mock_balancer_instance = Mock()
        mock_runner_instance = Mock()
        mock_balancer.return_value = mock_balancer_instance
        mock_runner.return_value = mock_runner_instance
        
        pool_manager = TorBalancerManager(
            config_builder=self.config_builder,
            checker=self.checker,
            runner=mock_runner_instance,
            http_balancer=mock_balancer_instance
        )
        
        self.assertTrue(hasattr(pool_manager.checker, 'test_exit_nodes_parallel'),
                       "TorPoolManager.checker missing test_exit_nodes_parallel method")
    
    def test_deprecated_methods_not_used(self):
        deprecated_methods = [
            'test_nodes_with_temp_instances'
        ]
        
        for method_name in deprecated_methods:
            with self.subTest(method=method_name):
                self.assertFalse(
                    hasattr(self.checker, method_name),
                    f"ExitNodeChecker should not have deprecated method: {method_name}"
                )
    
    @patch('exit_node_tester.TorInstance')
    @patch('exit_node_tester.requests.get')
    def test_exit_node_checker_integration_flow(self, mock_requests, mock_tor_instance_class):
        mock_response = Mock()
        mock_response.status_code = 200
        mock_requests.return_value = mock_response
        
        mock_instance = Mock()
        mock_instance.check_health.return_value = True
        mock_instance.get_proxies.return_value = {'http': 'socks5://127.0.0.1:9050'}
        mock_instance.reconfigure.return_value = True
        mock_instance.start.return_value = None
        mock_instance.stop.return_value = None
        mock_tor_instance_class.return_value = mock_instance
        
        exit_nodes = ['1.2.3.4', '5.6.7.8']
        result = self.checker.test_exit_nodes_parallel(exit_nodes, 2)
        
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) <= 2)
    
    def test_method_parameter_types(self):
        with patch('exit_node_tester.TorInstance'), \
             patch('exit_node_tester.requests.get') as mock_requests:
            
            mock_response = Mock()
            mock_response.status_code = 200
            mock_requests.return_value = mock_response
            
            try:
                result = self.checker.test_exit_nodes_parallel(['1.2.3.4'], 1)
                self.assertIsInstance(result, list)
            except Exception as e:
                self.fail(f"test_exit_nodes_parallel failed with correct parameters: {e}")
    
    def test_checker_configuration_completeness(self):
        required_attributes = [
            'test_url',
            'test_requests_count',
            'required_success_count',
            'timeout',
            'config_builder',
            'max_workers'
        ]
        
        for attr in required_attributes:
            with self.subTest(attribute=attr):
                self.assertTrue(
                    hasattr(self.checker, attr),
                    f"ExitNodeChecker missing required attribute: {attr}"
                )


if __name__ == '__main__':
    unittest.main()
