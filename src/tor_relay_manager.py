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
        
    def fetch_tor_relays(self):
        url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,exit_probability"
        response = requests.get(url, timeout=30)
        return response.json()
    
    def extract_relay_ips(self, relay_data):
        if not relay_data or 'relays' not in relay_data:
            return []

        exit_nodes = []
        seen_ips = set()
        
        for relay in relay_data['relays']:
            if 'or_addresses' in relay:
                exit_prob = relay.get('exit_probability', 0)
                
                if isinstance(exit_prob, str):
                    try:
                        exit_prob = float(exit_prob)
                    except (ValueError, TypeError):
                        exit_prob = 0

                if exit_prob > 0:
                    for addr in relay['or_addresses']:
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
        
        del seen_ips
        return exit_nodes
    
    def distribute_exit_nodes(self, num_processes: int) -> Dict[int, List[str]]:
        if not self.exit_nodes_by_probability:
            logger.warning("No exit nodes available for distribution")
            return {}
        
        nodes = self.exit_nodes_by_probability
        total_nodes = len(nodes)
        max_nodes_per_process = 25
        
        base_nodes_per_process = min(total_nodes // num_processes, max_nodes_per_process)
        extra_nodes = total_nodes % num_processes
        
        process_distributions = {}
        start_idx = 0
        
        for process_id in range(num_processes):
            nodes_count = min(
                base_nodes_per_process + (1 if process_id < extra_nodes else 0),
                max_nodes_per_process
            )
            
            if nodes_count > 0 and start_idx < total_nodes:
                end_idx = min(start_idx + nodes_count, total_nodes)
                process_nodes = nodes[start_idx:end_idx]
                process_distributions[process_id] = {
                    'exit_nodes': [node['ip'] for node in process_nodes],
                    'total_probability': sum(node['exit_probability'] for node in process_nodes),
                    'node_count': len(process_nodes)
                }
                start_idx = end_idx
            else:
                process_distributions[process_id] = {
                    'exit_nodes': [],
                    'total_probability': 0.0,
                    'node_count': 0
                }
        
        total_distributed = sum(data['node_count'] for data in process_distributions.values())
        logger.info(f"Distributed {total_distributed}/{total_nodes} nodes across {num_processes} processes (max {max_nodes_per_process} per process)")
        
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
                
            del ip_counts, duplicates, all_assigned_ips
            return False
        
        logger.info(f"Distribution validation passed: {unique_count} unique IPs assigned across all processes")
        del all_assigned_ips, unique_ips
        return True
