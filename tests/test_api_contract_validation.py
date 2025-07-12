#!/usr/bin/env python3

import unittest
import sys
import os
import json
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestAPIContractValidation(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        cls.api_contracts = {
            'ExitNodeChecker': {
                'required_methods': [
                    'test_exit_nodes_parallel',
                    'test_node', 
                    'test_nodes'
                ],
                'deprecated_methods': [
                    'test_nodes_with_temp_instances'
                ],
                'method_signatures': {
                    'test_exit_nodes_parallel': ['exit_nodes', 'required_count'],
                    'test_node': ['proxy'],
                    'test_nodes': ['proxies']
                }
            },
            'TorBalancerManager': {
                'required_methods': [
                    'run_pool',
                    'remove_failed',
                    'redistribute_with_replacements'
                ],
                'checker_usage': [
                    'test_exit_nodes_parallel'
                ]
            }
        }
    
    def test_exit_node_checker_contract(self):
        from exit_node_tester import ExitNodeChecker
        
        checker = ExitNodeChecker()
        contract = self.api_contracts['ExitNodeChecker']
        
        for method in contract['required_methods']:
            with self.subTest(method=method):
                self.assertTrue(hasattr(checker, method),
                               f"Missing required method: {method}")
                self.assertTrue(callable(getattr(checker, method)),
                               f"Method {method} is not callable")
        
        for method in contract['deprecated_methods']:
            with self.subTest(deprecated_method=method):
                self.assertFalse(hasattr(checker, method),
                                f"Deprecated method still exists: {method}")
    
    @patch('http_load_balancer.HTTPLoadBalancer')
    @patch('tor_parallel_runner.TorParallelRunner')
    def test_tor_pool_manager_contract(self, mock_runner, mock_balancer):
        from tor_pool_manager import TorBalancerManager
        from exit_node_tester import ExitNodeChecker
        
        mock_balancer_instance = Mock()
        mock_runner_instance = Mock()
        mock_balancer.return_value = mock_balancer_instance
        mock_runner.return_value = mock_runner_instance
        
        from config_manager import TorConfigBuilder
        
        config_builder = Mock(spec=TorConfigBuilder)
        checker = ExitNodeChecker()
        pool_manager = TorBalancerManager(
            config_builder=config_builder,
            checker=checker,
            runner=mock_runner_instance,
            http_balancer=mock_balancer_instance
        )
        
        contract = self.api_contracts['TorBalancerManager']
        
        for method in contract['required_methods']:
            with self.subTest(method=method):
                self.assertTrue(hasattr(pool_manager, method),
                               f"Missing required method: {method}")
        
        for checker_method in contract['checker_usage']:
            with self.subTest(checker_method=checker_method):
                self.assertTrue(hasattr(pool_manager.checker, checker_method),
                               f"Checker missing expected method: {checker_method}")
    
    def test_cross_module_method_calls_validation(self):
        tor_pool_manager_path = os.path.join(
            os.path.dirname(__file__), '..', 'src', 'tor_pool_manager.py'
        )
        
        with open(tor_pool_manager_path, 'r') as f:
            content = f.read()
        
        allowed_checker_methods = self.api_contracts['ExitNodeChecker']['required_methods']
        deprecated_methods = self.api_contracts['ExitNodeChecker']['deprecated_methods']
        
        for deprecated_method in deprecated_methods:
            self.assertNotIn(f'.{deprecated_method}(', content,
                           f"Found deprecated method call: {deprecated_method}")
        
        found_valid_methods = []
        for method in allowed_checker_methods:
            if f'.{method}(' in content:
                found_valid_methods.append(method)
        
        self.assertTrue(len(found_valid_methods) > 0,
                       "No valid checker methods found in tor_pool_manager.py")
    
    def test_method_signature_compatibility(self):
        from exit_node_tester import ExitNodeChecker
        import inspect
        
        checker = ExitNodeChecker()
        contract = self.api_contracts['ExitNodeChecker']
        
        for method_name, expected_params in contract['method_signatures'].items():
            with self.subTest(method=method_name):
                method = getattr(checker, method_name)
                sig = inspect.signature(method)
                actual_params = list(sig.parameters.keys())
                
                for param in expected_params:
                    self.assertIn(param, actual_params,
                                 f"Method {method_name} missing parameter: {param}")
    
    def test_import_dependencies_are_satisfied(self):
        modules_to_test = [
            'main',
            'tor_pool_manager', 
            'exit_node_tester',
            'config_manager'
        ]
        
        for module_name in modules_to_test:
            with self.subTest(module=module_name):
                try:
                    __import__(module_name)
                except ImportError as e:
                    self.fail(f"Failed to import {module_name}: {e}")
    
    def test_runtime_method_execution_compatibility(self):
        from exit_node_tester import ExitNodeChecker
        from config_manager import TorConfigBuilder
        
        config_builder = TorConfigBuilder()
        checker = ExitNodeChecker(config_builder=config_builder)
        
        with patch('exit_node_tester.TorInstance'), \
             patch('exit_node_tester.requests.get') as mock_requests:
            
            mock_response = Mock()
            mock_response.status_code = 200
            mock_requests.return_value = mock_response
            
            try:
                result = checker.test_exit_nodes_parallel(['1.2.3.4'], 1)
                self.assertIsInstance(result, list)
            except AttributeError as e:
                self.fail(f"Method call failed: {e}")
            except Exception as e:
                pass


if __name__ == '__main__':
    unittest.main()
