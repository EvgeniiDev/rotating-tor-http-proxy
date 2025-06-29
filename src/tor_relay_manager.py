import logging
import requests
import random
from collections import defaultdict
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class TorRelayManager:
    def __init__(self):
        self.current_relays = []
        self.exit_nodes_by_probability = []
        
    def fetch_tor_relays(self):
        try:
            url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,exit_probability"
            response = requests.get(url, timeout=30)
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            logger.error(f"Error fetching Tor relays: {e}")
            return None
    
    def extract_relay_ips(self, relay_data):
        if not relay_data or 'relays' not in relay_data:
            return []

        exit_nodes = []
        
        for relay in relay_data['relays']:
            if 'or_addresses' in relay:
                for addr in relay['or_addresses']:
                    ip = addr.split(':')[0]
                    if self._is_valid_ipv4(ip):
                        exit_prob = relay.get('exit_probability', 0)
                        
                        if isinstance(exit_prob, str):
                            try:
                                exit_prob = float(exit_prob)
                            except (ValueError, TypeError):
                                exit_prob = 0

                        if exit_prob > 0:
                            node_info = {
                                'ip': ip,
                                'country': relay.get('country', 'Unknown'),
                                'exit_probability': exit_prob
                            }
                            exit_nodes.append(node_info)

        exit_nodes.sort(key=lambda x: x['exit_probability'], reverse=True)
        
        logger.info(f"Found {len(exit_nodes)} IPv4 exit nodes with probability > 0")
        
        self.current_relays = exit_nodes
        self.exit_nodes_by_probability = exit_nodes
        
        return exit_nodes
    
    def _is_valid_ipv4(self, ip):
        try:
            parts = ip.split('.')
            if len(parts) != 4:
                return False
            for part in parts:
                if not (0 <= int(part) <= 255):
                    return False
            return True
        except (ValueError, AttributeError):
            return False
    
    def distribute_exit_nodes(self, num_processes: int) -> Dict[int, List[str]]:
        if not self.exit_nodes_by_probability:
            logger.warning("No exit nodes available for distribution")
            return {}
        
        sorted_nodes = sorted(self.exit_nodes_by_probability, key=lambda x: x['exit_probability'], reverse=True)
        
        process_distributions = {}
        for process_id in range(num_processes):
            process_distributions[process_id] = {
                'exit_nodes': [],
                'total_probability': 0.0,
                'node_count': 0
            }
        
        for i, node in enumerate(sorted_nodes):
            process_id = i % num_processes
            
            process_distributions[process_id]['exit_nodes'].append(node['ip'])
            process_distributions[process_id]['node_count'] += 1
            process_distributions[process_id]['total_probability'] += node['exit_probability']
        
        for process_id, data in process_distributions.items():
            avg_prob = data['total_probability'] / data['node_count'] if data['node_count'] > 0 else 0
            logger.info(f"Process {process_id}: {data['node_count']} nodes, "
                      f"avg prob: {avg_prob:.3f}, total prob: {data['total_probability']:.2f}")
        
        return process_distributions
