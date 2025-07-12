#!/usr/bin/env python3

import unittest
import sys
import os
import ast
import inspect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestModuleIntegrity(unittest.TestCase):
    
    def setUp(self):
        self.src_path = os.path.join(os.path.dirname(__file__), '..', 'src')
    
    def test_tor_pool_manager_imports_and_usage(self):
        tor_pool_manager_path = os.path.join(self.src_path, 'tor_pool_manager.py')
        
        with open(tor_pool_manager_path, 'r') as f:
            content = f.read()
        
        tree = ast.parse(content)
        
        method_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if (hasattr(node, 'attr') and 
                    isinstance(node.value, ast.Attribute) and
                    getattr(node.value, 'attr', None) == 'checker'):
                    method_calls.append(node.attr)
        
        deprecated_methods = ['test_nodes_with_temp_instances']
        for method in deprecated_methods:
            self.assertNotIn(method, method_calls,
                           f"tor_pool_manager.py still uses deprecated method: {method}")
        
        expected_methods = ['test_exit_nodes_parallel']
        for method in expected_methods:
            self.assertIn(method, method_calls,
                         f"tor_pool_manager.py should use method: {method}")
    
    def test_all_imports_are_valid(self):
        python_files = [
            'main.py',
            'config_manager.py',
            'exit_node_tester.py',
            'tor_pool_manager.py',
            'tor_process.py',
            'tor_relay_manager.py',
            'http_load_balancer.py',
            'tor_parallel_runner.py',
            'utils.py'
        ]
        
        for filename in python_files:
            with self.subTest(file=filename):
                file_path = os.path.join(self.src_path, filename)
                if os.path.exists(file_path):
                    try:
                        import importlib.util
                        spec = importlib.util.spec_from_file_location(
                            filename[:-3], file_path
                        )
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)
                    except Exception as e:
                        self.fail(f"Failed to import {filename}: {e}")
    
    def test_exit_node_checker_api_consistency(self):
        from exit_node_tester import ExitNodeChecker
        
        checker = ExitNodeChecker()
        
        critical_methods = {
            'test_exit_nodes_parallel': (list, int),
            'test_node': (dict,),
            'test_nodes': (list,)
        }
        
        for method_name, expected_param_types in critical_methods.items():
            with self.subTest(method=method_name):
                self.assertTrue(hasattr(checker, method_name))
                
                method = getattr(checker, method_name)
                sig = inspect.signature(method)
                params = list(sig.parameters.keys())
                
                if method_name == 'test_exit_nodes_parallel':
                    self.assertIn('exit_nodes', params)
                    self.assertIn('required_count', params)
                elif method_name == 'test_node':
                    self.assertIn('proxy', params)
                elif method_name == 'test_nodes':
                    self.assertIn('proxies', params)
    
    def test_class_interface_completeness(self):
        from exit_node_tester import ExitNodeChecker
        from tor_pool_manager import TorBalancerManager
        
        checker_methods = set(dir(ExitNodeChecker))
        required_public_methods = {
            'test_exit_nodes_parallel',
            'test_node',
            'test_nodes'
        }
        
        missing_methods = required_public_methods - checker_methods
        self.assertEqual(set(), missing_methods,
                        f"ExitNodeChecker missing methods: {missing_methods}")
        
        tor_pool_manager_methods = set(dir(TorBalancerManager))
        required_pool_methods = {
            'run_pool',
            'remove_failed',
            'redistribute_with_replacements'
        }
        
        missing_pool_methods = required_pool_methods - tor_pool_manager_methods
        self.assertEqual(set(), missing_pool_methods,
                        f"TorBalancerManager missing methods: {missing_pool_methods}")
    
    def test_method_call_patterns_in_source(self):
        patterns_to_avoid = [
            'test_nodes_with_temp_instances',
            'checker.nonexistent_method'
        ]
        
        files_to_check = ['tor_pool_manager.py', 'main.py']
        
        for filename in files_to_check:
            file_path = os.path.join(self.src_path, filename)
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    content = f.read()
                
                for pattern in patterns_to_avoid:
                    with self.subTest(file=filename, pattern=pattern):
                        self.assertNotIn(pattern, content,
                                       f"Found problematic pattern '{pattern}' in {filename}")


if __name__ == '__main__':
    unittest.main()
