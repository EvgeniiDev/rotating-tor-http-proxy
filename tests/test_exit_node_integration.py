import unittest
import tempfile
import os
import sys
import time
import subprocess
import shutil
from unittest.mock import Mock, patch

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from tor_pool_manager import TorPoolManager
from tor_instance_manager import TorInstanceManager
from config_manager import ConfigManager


class TestExitNodeMonitoringIntegration(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix='exit_monitor_test_')
        
        self.mock_load_balancer = Mock()
        self.mock_relay_manager = Mock()
        
        self.config_manager = ConfigManager()
        
        self.exit_nodes_1 = ['1.2.3.4', '5.6.7.8', '9.10.11.12']
        self.exit_nodes_2 = ['13.14.15.16', '17.18.19.20', '21.22.23.24']
        
    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
    
    def test_instance_exit_node_monitoring_integration(self):
        """Test exit node monitoring within a single TorInstanceManager"""
        instance = TorInstanceManager(
            port=9050,
            exit_nodes=self.exit_nodes_1,
            config_manager=self.config_manager
        )
        
        # Test initial state
        self.assertEqual(len(instance.exit_node_activity), 0)
        self.assertEqual(len(instance.suspicious_nodes), 0)
        self.assertEqual(len(instance.blacklisted_nodes), 0)
        
        # Simulate activity reporting
        instance._report_active_exit_node('1.2.3.4')
        instance._report_active_exit_node('5.6.7.8')
        
        # Check activity was recorded
        self.assertEqual(len(instance.exit_node_activity), 2)
        self.assertIn('1.2.3.4', instance.exit_node_activity)
        self.assertIn('5.6.7.8', instance.exit_node_activity)
        
        # Test blacklisting
        instance.blacklist_exit_node('1.2.3.4')
        self.assertIn('1.2.3.4', instance.blacklisted_nodes)
        self.assertNotIn('1.2.3.4', instance.exit_node_activity)
        
        # Test healthy nodes filtering
        healthy = instance.get_healthy_exit_nodes()
        self.assertNotIn('1.2.3.4', healthy)  # Blacklisted
        self.assertIn('5.6.7.8', healthy)     # Still healthy
        self.assertIn('9.10.11.12', healthy)  # Never used but not blacklisted
        
        # Test stats generation
        stats = instance.get_exit_node_stats()
        self.assertEqual(stats['port'], 9050)
        self.assertEqual(stats['total_tracked_nodes'], 1)  # Only active (non-blacklisted)
        self.assertEqual(stats['blacklisted_nodes'], 1)
    
    def test_pool_manager_global_monitoring_integration(self):
        """Test global exit node monitoring across multiple instances"""
        pool_manager = TorPoolManager(
            config_manager=self.config_manager,
            load_balancer=self.mock_load_balancer,
            relay_manager=self.mock_relay_manager
        )
        
        # Create mock instances
        instance1 = Mock()
        instance1.get_exit_node_stats.return_value = {
            'port': 9050,
            'total_tracked_nodes': 2,
            'active_nodes': 2,
            'inactive_nodes': 0,
            'suspicious_nodes': 0,
            'blacklisted_nodes': 0,
            'most_used_nodes': [('1.2.3.4', 3), ('5.6.7.8', 2)]
        }
        instance1.get_suspicious_exit_nodes.return_value = []
        
        instance2 = Mock()
        instance2.get_exit_node_stats.return_value = {
            'port': 9051,
            'total_tracked_nodes': 1,
            'active_nodes': 0,
            'inactive_nodes': 1,
            'suspicious_nodes': 1,
            'blacklisted_nodes': 0,
            'most_used_nodes': [('13.14.15.16', 1)]
        }
        instance2.get_suspicious_exit_nodes.return_value = ['13.14.15.16']
        
        # Add instances to pool
        pool_manager.instances[9050] = instance1
        pool_manager.instances[9051] = instance2
        
        # Test global stats aggregation
        global_stats = pool_manager.get_exit_node_global_stats()
        totals = global_stats['global_totals']
        
        self.assertEqual(totals['total_tracked_nodes'], 3)
        self.assertEqual(totals['active_nodes'], 2)
        self.assertEqual(totals['inactive_nodes'], 1)
        self.assertEqual(totals['suspicious_nodes'], 1)
        
        # Test global suspicious node tracking
        pool_manager._update_global_exit_node_monitoring()
        self.assertIn('13.14.15.16', pool_manager.global_suspicious_nodes)
        
        # Test global blacklisting
        pool_manager.blacklist_exit_node_globally('1.2.3.4')
        self.assertIn('1.2.3.4', pool_manager.global_blacklisted_nodes)
        
        # Check that both instances were called to blacklist
        instance1.blacklist_exit_node.assert_called_once_with('1.2.3.4')
        instance2.blacklist_exit_node.assert_called_once_with('1.2.3.4')
    
    def test_pool_manager_stats_include_monitoring(self):
        """Test that pool manager stats include exit node monitoring data"""
        pool_manager = TorPoolManager(
            config_manager=self.config_manager,
            load_balancer=self.mock_load_balancer,
            relay_manager=self.mock_relay_manager
        )
        
        # Mock instances with monitoring data
        mock_instance = Mock()
        mock_instance.get_exit_node_stats.return_value = {
            'port': 9050,
            'total_tracked_nodes': 5,
            'active_nodes': 3,
            'inactive_nodes': 2,
            'suspicious_nodes': 1,
            'blacklisted_nodes': 1,
            'most_used_nodes': []
        }
        
        pool_manager.instances[9050] = mock_instance
        
        # Get overall stats
        stats = pool_manager.get_stats()
        
        # Check that exit node monitoring data is included
        self.assertIn('exit_node_monitoring', stats)
        monitoring_stats = stats['exit_node_monitoring']
        
        self.assertEqual(monitoring_stats['total_tracked_nodes'], 5)
        self.assertEqual(monitoring_stats['active_nodes'], 3)
        self.assertEqual(monitoring_stats['suspicious_nodes'], 1)
        self.assertEqual(monitoring_stats['blacklisted_nodes'], 1)
    
    @patch('requests.get')
    def test_redistribution_with_monitoring_filters(self, mock_get):
        """Test that redistribution properly filters out problematic nodes"""
        pool_manager = TorPoolManager(
            config_manager=self.config_manager,
            load_balancer=self.mock_load_balancer,
            relay_manager=self.mock_relay_manager
        )
        
        # Mock relay manager to return new nodes
        self.mock_relay_manager.fetch_tor_relays.return_value = {'mock': 'data'}
        self.mock_relay_manager.extract_relay_ips.return_value = [
            '1.2.3.4', '5.6.7.8', '9.10.11.12', '13.14.15.16',
            '17.18.19.20', '21.22.23.24', '25.26.27.28'
        ]
        
        # Create mock instance that needs redistribution
        mock_instance = Mock()
        mock_instance.is_running = True
        mock_instance.exit_nodes = ['1.2.3.4', '5.6.7.8', '9.10.11.12', '13.14.15.16']
        mock_instance.get_healthy_exit_nodes.return_value = ['1.2.3.4']  # Only 1 healthy
        mock_instance.reload_exit_nodes.return_value = True
        
        pool_manager.instances[9050] = mock_instance
        pool_manager.running = True
        
        # Add problematic nodes to global lists
        pool_manager.global_blacklisted_nodes.add('5.6.7.8')
        pool_manager.global_suspicious_nodes.add('9.10.11.12')
        
        # Run redistribution
        pool_manager.redistribute_nodes()
        
        # Verify reload was called
        mock_instance.reload_exit_nodes.assert_called_once()
        
        # Verify that problematic nodes were filtered out
        called_args = mock_instance.reload_exit_nodes.call_args[0][0]
        self.assertNotIn('5.6.7.8', called_args)  # Blacklisted
        self.assertNotIn('9.10.11.12', called_args)  # Suspicious
        
        # Verify that good nodes are included
        self.assertIn('1.2.3.4', called_args)
        self.assertIn('13.14.15.16', called_args)
    
    def test_instance_status_includes_monitoring(self):
        """Test that instance status includes exit node monitoring data"""
        instance = TorInstanceManager(
            port=9050,
            exit_nodes=self.exit_nodes_1,
            config_manager=self.config_manager
        )
        
        # Add some activity
        instance._report_active_exit_node('1.2.3.4')
        instance.blacklist_exit_node('5.6.7.8')
        
        # Get status
        status = instance.get_status()
        
        # Verify monitoring data is included
        self.assertIn('exit_node_monitoring', status)
        monitoring = status['exit_node_monitoring']
        
        self.assertEqual(monitoring['port'], 9050)
        self.assertEqual(monitoring['total_tracked_nodes'], 1)  # Only active tracked
        self.assertEqual(monitoring['blacklisted_nodes'], 1)


if __name__ == '__main__':
    unittest.main()
