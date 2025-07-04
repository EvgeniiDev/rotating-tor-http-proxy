import logging
import requests
from typing import List, Dict, Any, Optional
from utils import is_valid_ipv4

logger = logging.getLogger(__name__)


class TorRelayManager:
    __slots__ = ('current_relays', 'exit_nodes_by_probability')
    
    def __init__(self):
        self.current_relays = []
        self.exit_nodes_by_probability = []
        
    def fetch_tor_relays(self) -> Optional[Dict]:
        url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,exit_probability"
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logger.error(f"Failed to fetch Tor relays: {e}")
            return None

    def extract_relay_ips(self, relay_data: Dict) -> List[Dict]:
        if not relay_data or 'relays' not in relay_data:
            return []

        exit_nodes = []
        seen_ips = set()

        for relay in relay_data['relays']:
            exit_prob = relay.get('exit_probability', 0)
            if not (isinstance(exit_prob, (int, float)) and exit_prob > 0):
                continue

            for addr in relay.get('or_addresses', []):
                ip = addr.split(':')[0]
                if is_valid_ipv4(ip) and ip not in seen_ips:
                    seen_ips.add(ip)
                    node_info = {
                        'ip': ip,
                        'country': relay.get('country', 'Unknown'),
                        'exit_probability': exit_prob
                    }
                    exit_nodes.append(node_info)
                    break
        
        exit_nodes.sort(key=lambda x: x['exit_probability'], reverse=True)
        
        logger.info(f"Found {len(exit_nodes)} unique IPv4 exit nodes with probability > 0")
        
        self.current_relays = exit_nodes
        self.exit_nodes_by_probability = exit_nodes
        
        return exit_nodes
    
    def distribute_exit_nodes(self, num_processes: int) -> Dict[int, List[str]]:
        if not self.exit_nodes_by_probability:
            logger.warning("No exit nodes available for distribution")
            return {}
        
        return self.distribute_exit_nodes_for_specific_instances(
            list(range(num_processes)), self.exit_nodes_by_probability
        )

    def distribute_exit_nodes_for_specific_instances(self, process_ids: List[int], available_nodes: List[Dict]) -> Dict[int, Dict]:
        if not available_nodes:
            logger.warning("No available nodes for distribution")
            return {}

        num_processes = len(process_ids)
        total_nodes = len(available_nodes)
        max_nodes_per_process = 25

        if total_nodes < num_processes:
            logger.warning(f"Number of available nodes ({total_nodes}) is less than requested processes ({num_processes}). Some processes may not get nodes.")

        process_distributions = {pid: {'exit_nodes': [], 'total_probability': 0.0, 'node_count': 0} for pid in process_ids}

        for i in range(total_nodes):
            process_id = process_ids[i % num_processes]
            
            if process_distributions[process_id]['node_count'] < max_nodes_per_process:
                node = available_nodes[i]
                process_distributions[process_id]['exit_nodes'].append(node['ip'])
                process_distributions[process_id]['total_probability'] += node['exit_probability']
                process_distributions[process_id]['node_count'] += 1

        total_distributed = sum(data['node_count'] for data in process_distributions.values())
        logger.info(f"Distributed {total_distributed}/{total_nodes} nodes across {num_processes} specific processes (max {max_nodes_per_process} per process)")
        
        if not self.validate_distribution_uniqueness(process_distributions):
            logger.error("Distribution validation failed - duplicate IPs detected")
            return {}
        
        for process_id, data in process_distributions.items():
            if data['node_count'] > 0:
                avg_prob = data['total_probability'] / data['node_count']
                logger.info(f"Process {process_id}: {data['node_count']} nodes, avg prob: {avg_prob:.4f}")
        
        return process_distributions
    
    def validate_distribution_uniqueness(self, distributions: Dict[int, Dict]) -> bool:
        all_assigned_ips = []
        
        for process_id, data in distributions.items():
            exit_nodes = data.get('exit_nodes', [])
            all_assigned_ips.extend(exit_nodes)
        
        unique_ips = set(all_assigned_ips)
        total_assigned = len(all_assigned_ips)
        unique_count = len(unique_ips)
        
        if total_assigned != unique_count:
            ip_counts = {}
            for ip in all_assigned_ips:
                ip_counts[ip] = ip_counts.get(ip, 0) + 1
            
            duplicates = [(ip, count) for ip, count in ip_counts.items() if count > 1]
            
            logger.error(f"Found {total_assigned - unique_count} duplicate IP assignments:")
            for ip, count in duplicates[:5]:
                logger.error(f"  IP {ip} assigned {count} times")
                
            return False
        
        logger.info(f"Distribution validation passed: {unique_count} unique IPs assigned across all processes")
        return True
