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
        self.distributed_nodes = {}
        
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
        
        total_nodes = len(self.exit_nodes_by_probability)
        nodes_per_process = min(50, max(10, total_nodes // num_processes))
        
        process_distributions = {}
        
        high_prob_nodes = [node for node in self.exit_nodes_by_probability if node['exit_probability'] > 0.5]
        medium_prob_nodes = [node for node in self.exit_nodes_by_probability if 0.1 < node['exit_probability'] <= 0.5]
        low_prob_nodes = [node for node in self.exit_nodes_by_probability if 0 < node['exit_probability'] <= 0.1]
        
        logger.info(f"Node distribution: {len(high_prob_nodes)} high, {len(medium_prob_nodes)} medium, {len(low_prob_nodes)} low probability")
        
        all_nodes = high_prob_nodes + medium_prob_nodes + low_prob_nodes
        random.shuffle(all_nodes)
        
        for process_id in range(num_processes):
            start_idx = process_id * nodes_per_process
            end_idx = min(start_idx + nodes_per_process, len(all_nodes))
            
            if start_idx < len(all_nodes):
                process_nodes = all_nodes[start_idx:end_idx]
                
                high_count = sum(1 for node in process_nodes if node['exit_probability'] > 0.5)
                total_prob = sum(node['exit_probability'] for node in process_nodes)
                
                process_distributions[process_id] = {
                    'exit_nodes': [node['ip'] for node in process_nodes],
                    'high_probability_count': high_count,
                    'total_probability': total_prob,
                    'node_count': len(process_nodes)
                }
                
                logger.info(f"Process {process_id}: {len(process_nodes)} nodes, "
                          f"{high_count} high-prob, total prob: {total_prob:.2f}")
        
        self.distributed_nodes = process_distributions
        return process_distributions
    
    def get_exit_nodes_for_process(self, process_id: int) -> List[str]:
        if process_id in self.distributed_nodes:
            return self.distributed_nodes[process_id]['exit_nodes']
        return []
    
    def get_distribution_stats(self):
        if not self.distributed_nodes:
            return {}
        
        stats = {}
        for process_id, data in self.distributed_nodes.items():
            stats[process_id] = {
                'node_count': data['node_count'],
                'high_probability_count': data['high_probability_count'],
                'total_probability': data['total_probability'],
                'avg_probability': data['total_probability'] / data['node_count'] if data['node_count'] > 0 else 0
            }
        
        return stats
