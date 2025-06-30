import logging
import requests
from typing import List, Dict, Any, Optional
from utils import is_valid_ipv4

logger = logging.getLogger(__name__)


class TorRelayManager:
    def __init__(self):
        self.current_relays = []
        self.exit_nodes_by_probability = []
        
    def fetch_tor_relays(self):
        url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,exit_probability"
        response = requests.get(url, timeout=30)
        return response.json()
    
    def extract_relay_ips(self, relay_data):
        if not relay_data or 'relays' not in relay_data:
            return []

        exit_nodes = []
        
        for relay in relay_data['relays']:
            if 'or_addresses' in relay:
                for addr in relay['or_addresses']:
                    ip = addr.split(':')[0]
                    if is_valid_ipv4(ip):
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
        
        nodes = self.exit_nodes_by_probability
        total_nodes = len(nodes)
        base_nodes_per_process = total_nodes // num_processes
        extra_nodes = total_nodes % num_processes
        process_distributions = {}
        start_idx = 0
        
        for process_id in range(num_processes):
            nodes_count = base_nodes_per_process + (1 if process_id < extra_nodes else 0)
            
            if nodes_count > 0:
                process_nodes = nodes[start_idx:start_idx + nodes_count]
                process_distributions[process_id] = {
                    'exit_nodes': [node['ip'] for node in process_nodes],
                    'total_probability': sum(node['exit_probability'] for node in process_nodes),
                    'node_count': nodes_count
                }
                start_idx += nodes_count
            else:
                process_distributions[process_id] = {
                    'exit_nodes': [],
                    'total_probability': 0.0,
                    'node_count': 0
                }
        
        for process_id, data in process_distributions.items():
            if data['node_count'] > 0:
                avg_prob = data['total_probability'] / data['node_count']
                logger.info(f"Process {process_id}: {data['node_count']} nodes, avg prob: {avg_prob:.4f}")
        
        return process_distributions
