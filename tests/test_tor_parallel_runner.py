#!/usr/bin/env python3
"""
Unit tests for TorParallelRunner class
"""
import unittest
import tempfile
import shutil
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from tor_parallel_runner import TorParallelRunner
from config_manager import TorConfigBuilder


class TestTorParallelRunner(unittest.TestCase):
    """Test cases for TorParallelRunner class"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.config_builder = TorConfigBuilder()
        self.runner = TorParallelRunner(self.config_builder, max_concurrent=3)  # Small limit for testing
        
    def tearDown(self):
        """Clean up after tests"""
        self.runner.stop_all()
        
    @patch('tor_parallel_runner.TorInstance')
    def test_start_many_respects_max_concurrent_limit(self, mock_tor_instance):
        """Test that start_many respects the max_concurrent limit"""
        # Mock TorInstance
        mock_instance = Mock()
        mock_instance.create_config.return_value = True
        mock_instance.start.return_value = True
        mock_tor_instance.return_value = mock_instance
        
        # Try to start 5 processes with max_concurrent=3
        ports = [9050, 9051, 9052, 9053, 9054]
        exit_nodes_list = [["node1"], ["node2"], ["node3"], ["node4"], ["node5"]]
        
        self.runner.start_many(ports, exit_nodes_list)
        
        # Should only create 3 instances (max_concurrent limit)
        self.assertEqual(len(self.runner.instances), 3)
        self.assertEqual(mock_tor_instance.call_count, 3)
        
    @patch('tor_parallel_runner.TorInstance')
    def test_start_many_creates_correct_processes(self, mock_tor_instance):
        """Test that start_many creates processes with correct parameters"""
        # Mock TorInstance
        mock_instance = Mock()
        mock_instance.create_config.return_value = True
        mock_instance.start.return_value = True
        mock_tor_instance.return_value = mock_instance
        
        ports = [9050, 9051]
        exit_nodes_list = [["node1", "node2"], ["node3", "node4"]]
        
        self.runner.start_many(ports, exit_nodes_list)
        
        # Verify instances were created with correct parameters
        self.assertEqual(len(self.runner.instances), 2)
        
        # Check that TorInstance was called with correct parameters
        calls = mock_tor_instance.call_args_list
        self.assertEqual(calls[0][0][0], 9050)  # First port
        self.assertEqual(calls[0][0][1], ["node1", "node2"])  # First exit nodes
        self.assertEqual(calls[1][0][0], 9051)  # Second port
        self.assertEqual(calls[1][0][1], ["node3", "node4"])  # Second exit nodes
        
    @patch('tor_parallel_runner.TorInstance')
    def test_get_statuses_returns_correct_data(self, mock_tor_instance):
        """Test that get_statuses returns correct status data"""
        # Mock TorInstance
        mock_instance1 = Mock()
        mock_instance1.is_running = True
        mock_instance1.create_config.return_value = True
        mock_instance1.start.return_value = True
        
        mock_instance2 = Mock()
        mock_instance2.is_running = False
        mock_instance2.create_config.return_value = True
        mock_instance2.start.return_value = True
        
        mock_tor_instance.side_effect = [mock_instance1, mock_instance2]
        
        ports = [9050, 9051]
        exit_nodes_list = [["node1"], ["node2"]]
        
        self.runner.start_many(ports, exit_nodes_list)
        statuses = self.runner.get_statuses()
        
        self.assertEqual(len(statuses), 2)
        self.assertEqual(statuses[9050]['is_running'], True)
        self.assertEqual(statuses[9051]['is_running'], False)
        
    @patch('tor_parallel_runner.TorInstance')
    def test_stop_all_stops_all_instances(self, mock_tor_instance):
        """Test that stop_all properly stops all instances"""
        # Mock TorInstance
        mock_instance = Mock()
        mock_instance.create_config.return_value = True
        mock_instance.start.return_value = True
        mock_tor_instance.return_value = mock_instance
        
        ports = [9050, 9051]
        exit_nodes_list = [["node1"], ["node2"]]
        
        self.runner.start_many(ports, exit_nodes_list)
        self.assertEqual(len(self.runner.instances), 2)
        
        self.runner.stop_all()
        
        # Verify stop was called on all instances
        self.assertEqual(mock_instance.stop.call_count, 2)
        self.assertEqual(len(self.runner.instances), 0)
        
    @patch('tor_parallel_runner.TorInstance')
    def test_restart_failed_restarts_only_failed_instances(self, mock_tor_instance):
        """Test that restart_failed only restarts instances that are not running"""
        # Mock TorInstance
        mock_instance1 = Mock()
        mock_instance1.is_running = False  # Failed
        mock_instance1.create_config.return_value = True
        mock_instance1.start.return_value = True
        
        mock_instance2 = Mock()
        mock_instance2.is_running = True  # Healthy
        mock_instance2.create_config.return_value = True
        mock_instance2.start.return_value = True
        
        mock_tor_instance.side_effect = [mock_instance1, mock_instance2]
        
        ports = [9050, 9051]
        exit_nodes_list = [["node1"], ["node2"]]
        
        self.runner.start_many(ports, exit_nodes_list)
        self.runner.restart_failed()
        
        # Only the failed instance should be restarted
        self.assertEqual(mock_instance1.stop.call_count, 1)
        self.assertEqual(mock_instance1.create_config.call_count, 2)  # Initial + restart
        self.assertEqual(mock_instance1.start.call_count, 2)  # Initial + restart
        
        # Healthy instance should not be restarted
        self.assertEqual(mock_instance2.stop.call_count, 0)
        self.assertEqual(mock_instance2.create_config.call_count, 1)  # Only initial
        self.assertEqual(mock_instance2.start.call_count, 1)  # Only initial
        
    def test_max_concurrent_initialization(self):
        """Test that max_concurrent is properly initialized"""
        runner1 = TorParallelRunner(self.config_builder, max_concurrent=5)
        self.assertEqual(runner1.max_concurrent, 5)
        
        runner2 = TorParallelRunner(self.config_builder)  # Default should be 20
        self.assertEqual(runner2.max_concurrent, 20)


if __name__ == '__main__':
    unittest.main()