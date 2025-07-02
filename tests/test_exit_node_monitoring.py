import unittest
import time
import sys
import os
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from tor_instance_manager import TorInstanceManager
from tor_pool_manager import TorPoolManager


class TestExitNodeMonitoring(unittest.TestCase):
    def setUp(self):
        self.mock_config_manager = Mock()
        self.mock_config_manager.get_tor_config_by_port.return_value = "mock config"
        
        self.exit_nodes = ['1.2.3.4', '5.6.7.8', '9.10.11.12']
        
    def test_tor_instance_exit_node_reporting(self):
        """Test that TorInstanceManager properly reports active exit nodes"""
        instance = TorInstanceManager(
            port=9050,
            exit_nodes=self.exit_nodes,
            config_manager=self.mock_config_manager
        )
        
        # Test reporting active node
        instance._report_active_exit_node('1.2.3.4')
        
        # Check that node was recorded
        self.assertIn('1.2.3.4', instance.exit_node_activity)
        self.assertEqual(instance.node_usage_count['1.2.3.4'], 1)
        
        # Test multiple reports increase usage count
        instance._report_active_exit_node('1.2.3.4')
        self.assertEqual(instance.node_usage_count['1.2.3.4'], 2)
    
    def test_tor_instance_suspicious_node_recovery(self):
        """Test that suspicious nodes can recover when they become active"""
        instance = TorInstanceManager(
            port=9050,
            exit_nodes=self.exit_nodes,
            config_manager=self.mock_config_manager
        )
        
        # Mark node as suspicious
        instance.suspicious_nodes.add('1.2.3.4')
        self.assertIn('1.2.3.4', instance.suspicious_nodes)
        
        # Report it as active
        instance._report_active_exit_node('1.2.3.4')
        
        # Should be removed from suspicious list
        self.assertNotIn('1.2.3.4', instance.suspicious_nodes)
    
    def test_tor_instance_inactive_node_detection(self):
        """Test detection of inactive exit nodes"""
        instance = TorInstanceManager(
            port=9050,
            exit_nodes=self.exit_nodes,
            config_manager=self.mock_config_manager
        )
        
        # Set a short threshold for testing
        instance.inactive_threshold = timedelta(seconds=1)
        
        # Report active node
        instance._report_active_exit_node('1.2.3.4')
        
        # Wait for threshold to pass
        time.sleep(1.1)
        
        # Check inactive nodes
        inactive = instance.get_inactive_exit_nodes()
        self.assertIn('1.2.3.4', inactive)
        
        # Run check inactive nodes to mark as suspicious
        instance._check_inactive_exit_nodes()
        self.assertIn('1.2.3.4', instance.suspicious_nodes)
    
    def test_tor_instance_blacklisting(self):
        """Test exit node blacklisting functionality"""
        instance = TorInstanceManager(
            port=9050,
            exit_nodes=self.exit_nodes,
            config_manager=self.mock_config_manager
        )
        
        # Report active node first
        instance._report_active_exit_node('1.2.3.4')
        
        # Blacklist the node
        instance.blacklist_exit_node('1.2.3.4')
        
        # Check it's in blacklist and removed from activity
        self.assertIn('1.2.3.4', instance.blacklisted_nodes)
        self.assertNotIn('1.2.3.4', instance.exit_node_activity)
        
        # Check health status
        self.assertFalse(instance.is_exit_node_healthy('1.2.3.4'))
    
    def test_tor_instance_healthy_nodes_filter(self):
        """Test filtering of healthy exit nodes"""
        instance = TorInstanceManager(
            port=9050,
            exit_nodes=self.exit_nodes,
            config_manager=self.mock_config_manager
        )
        
        # Blacklist one node and make another suspicious
        instance.blacklist_exit_node('1.2.3.4')
        instance.suspicious_nodes.add('5.6.7.8')
        
        # Get healthy nodes
        healthy = instance.get_healthy_exit_nodes()
        
        # Should only contain the non-blacklisted, non-suspicious node
        self.assertEqual(len(healthy), 1)
        self.assertIn('9.10.11.12', healthy)
        self.assertNotIn('1.2.3.4', healthy)
        self.assertNotIn('5.6.7.8', healthy)
    
    def test_tor_instance_stats_generation(self):
        """Test exit node statistics generation"""
        instance = TorInstanceManager(
            port=9050,
            exit_nodes=self.exit_nodes,
            config_manager=self.mock_config_manager
        )
        
        # Report some activity
        instance._report_active_exit_node('1.2.3.4')
        instance._report_active_exit_node('1.2.3.4')  # Increase usage count
        instance._report_active_exit_node('5.6.7.8')
        
        # Blacklist one node
        instance.blacklist_exit_node('9.10.11.12')
        
        # Get stats
        stats = instance.get_exit_node_stats()
        
        self.assertEqual(stats['port'], 9050)
        self.assertEqual(stats['total_tracked_nodes'], 2)  # Only active nodes
        self.assertEqual(stats['active_nodes'], 2)
        self.assertEqual(stats['blacklisted_nodes'], 1)
        
        # Check most used nodes
        most_used = stats['most_used_nodes']
        self.assertEqual(most_used[0][0], '1.2.3.4')  # Most used
        self.assertEqual(most_used[0][1], 2)  # Usage count


class TestTorPoolManagerExitNodeMonitoring(unittest.TestCase):
    def setUp(self):
        self.mock_config_manager = Mock()
        self.mock_load_balancer = Mock()
        self.mock_relay_manager = Mock()
        
        self.pool_manager = TorPoolManager(
            config_manager=self.mock_config_manager,
            load_balancer=self.mock_load_balancer,
            relay_manager=self.mock_relay_manager
        )
    
    def test_global_exit_node_stats_aggregation(self):
        """Test aggregation of exit node stats across all instances"""
        # Create mock instances
        mock_instance1 = Mock()
        mock_instance1.get_exit_node_stats.return_value = {
            'port': 9050,
            'total_tracked_nodes': 3,
            'active_nodes': 2,
            'inactive_nodes': 1,
            'suspicious_nodes': 1,
            'blacklisted_nodes': 0,
            'most_used_nodes': [('1.2.3.4', 5)]
        }
        
        mock_instance2 = Mock()
        mock_instance2.get_exit_node_stats.return_value = {
            'port': 9051,
            'total_tracked_nodes': 2,
            'active_nodes': 1,
            'inactive_nodes': 1,
            'suspicious_nodes': 0,
            'blacklisted_nodes': 1,
            'most_used_nodes': [('5.6.7.8', 3)]
        }
        
        # Add instances to pool
        self.pool_manager.instances[9050] = mock_instance1
        self.pool_manager.instances[9051] = mock_instance2
        
        # Get global stats
        global_stats = self.pool_manager.get_exit_node_global_stats()
        
        # Check aggregated totals
        totals = global_stats['global_totals']
        self.assertEqual(totals['total_tracked_nodes'], 5)
        self.assertEqual(totals['active_nodes'], 3)
        self.assertEqual(totals['inactive_nodes'], 2)
        self.assertEqual(totals['suspicious_nodes'], 1)
        self.assertEqual(totals['blacklisted_nodes'], 1)
    
    def test_global_blacklisting(self):
        """Test global blacklisting across all instances"""
        # Create mock instances
        mock_instance1 = Mock()
        mock_instance2 = Mock()
        
        # Add instances to pool
        self.pool_manager.instances[9050] = mock_instance1
        self.pool_manager.instances[9051] = mock_instance2
        
        # Blacklist node globally
        self.pool_manager.blacklist_exit_node_globally('1.2.3.4')
        
        # Check that all instances were called
        mock_instance1.blacklist_exit_node.assert_called_once_with('1.2.3.4')
        mock_instance2.blacklist_exit_node.assert_called_once_with('1.2.3.4')
        
        # Check global tracking
        self.assertIn('1.2.3.4', self.pool_manager.global_blacklisted_nodes)
    
    def test_global_suspicious_node_tracking(self):
        """Test tracking of suspicious nodes across instances"""
        # Create mock instances
        mock_instance1 = Mock()
        mock_instance1.get_suspicious_exit_nodes.return_value = ['1.2.3.4', '5.6.7.8']
        
        mock_instance2 = Mock()
        mock_instance2.get_suspicious_exit_nodes.return_value = ['9.10.11.12']
        
        # Add instances to pool
        self.pool_manager.instances[9050] = mock_instance1
        self.pool_manager.instances[9051] = mock_instance2
        
        # Update global monitoring
        self.pool_manager._update_global_exit_node_monitoring()
        
        # Check that suspicious nodes were tracked globally
        self.assertIn('1.2.3.4', self.pool_manager.global_suspicious_nodes)
        self.assertIn('5.6.7.8', self.pool_manager.global_suspicious_nodes)
        self.assertIn('9.10.11.12', self.pool_manager.global_suspicious_nodes)
    
    @patch('requests.get')
    def test_redistribute_nodes_with_monitoring(self, mock_get):
        """Test node redistribution considers monitoring data"""
        # Mock relay manager - provide more nodes than current instance has
        self.mock_relay_manager.fetch_tor_relays.return_value = {'mock': 'data'}
        self.mock_relay_manager.extract_relay_ips.return_value = [
            '1.2.3.4', '5.6.7.8', '9.10.11.12', '13.14.15.16', 
            '17.18.19.20', '21.22.23.24', '25.26.27.28', '29.30.31.32'
        ]
        
        # Create mock instance with few healthy nodes
        mock_instance = Mock()
        mock_instance.is_running = True
        mock_instance.exit_nodes = ['1.2.3.4', '5.6.7.8', '9.10.11.12', '13.14.15.16']
        mock_instance.get_healthy_exit_nodes.return_value = ['1.2.3.4']  # Only 1 healthy out of 4
        mock_instance.reload_exit_nodes.return_value = True
        
        # Add to pool
        self.pool_manager.instances[9050] = mock_instance
        self.pool_manager.running = True
        
        # Add some nodes to global blacklist/suspicious (but not too many)
        self.pool_manager.global_blacklisted_nodes.add('5.6.7.8')
        self.pool_manager.global_suspicious_nodes.add('9.10.11.12')
        
        # Run redistribution
        self.pool_manager.redistribute_nodes()
        
        # Check that reload was called (instance has low healthy ratio)
        mock_instance.reload_exit_nodes.assert_called_once()
        
        # Check that called with filtered nodes (excluding blacklisted/suspicious)
        called_args = mock_instance.reload_exit_nodes.call_args[0][0]
        self.assertNotIn('5.6.7.8', called_args)  # Blacklisted
        self.assertNotIn('9.10.11.12', called_args)  # Suspicious


if __name__ == '__main__':
    unittest.main()
