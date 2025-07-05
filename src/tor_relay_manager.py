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
        url = (
            "https://onionoo.torproject.org/details?type=relay&running=true&fields="
            "or_addresses,country,exit_probability,exit_policy_summary,last_seen,uptime,flags"
        )
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
            flags = relay.get('flags', [])
            if 'Exit' not in flags:
                continue

            exit_prob = relay.get('exit_probability', 0)
            if not (isinstance(exit_prob, (int, float)) and exit_prob > 0):
                continue

            exit_policy = relay.get('exit_policy_summary', {})
            if not self._check_exit_policy_for_web_traffic(exit_policy):
                continue

            if not self._check_node_stability(relay):
                continue

            for addr in relay.get('or_addresses', []):
                ip = addr.split(':')[0]
                if is_valid_ipv4(ip) and ip not in seen_ips:
                    seen_ips.add(ip)
                    exit_nodes.append({
                        'ip': ip,
                        'country': relay.get('country', 'Unknown'),
                        'exit_probability': exit_prob,
                        'flags': flags,
                        'uptime': relay.get('uptime', 0),
                        'last_seen': relay.get('last_seen', ''),
                        'exit_policy_summary': exit_policy
                    })
                    break

        logger.info(f"Found {len(exit_nodes)} qualified exit nodes after filtering")

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
        max_nodes_per_process = 25
        process_distributions = {pid: {'exit_nodes': [], 'total_probability': 0.0, 'node_count': 0} for pid in process_ids}

        for i, node in enumerate(available_nodes):
            process_id = process_ids[i % num_processes]
            if process_distributions[process_id]['node_count'] < max_nodes_per_process:
                process_distributions[process_id]['exit_nodes'].append(node['ip'])
                process_distributions[process_id]['total_probability'] += node['exit_probability']
                process_distributions[process_id]['node_count'] += 1

        total_distributed = sum(data['node_count'] for data in process_distributions.values())
        logger.info(f"Distributed {total_distributed} nodes across {num_processes} processes")

        if not self.validate_distribution_uniqueness(process_distributions):
            logger.error("Distribution validation failed - duplicate IPs detected")
            return {}

        return process_distributions

    def validate_distribution_uniqueness(self, distributions: Dict[int, Dict]) -> bool:
        all_assigned_ips = [ip for data in distributions.values() for ip in data.get('exit_nodes', [])]
        unique_ips = set(all_assigned_ips)
        if len(all_assigned_ips) != len(unique_ips):
            ip_counts = {}
            for ip in all_assigned_ips:
                ip_counts[ip] = ip_counts.get(ip, 0) + 1
            duplicates = [(ip, count) for ip, count in ip_counts.items() if count > 1]
            logger.error(f"Found {len(all_assigned_ips) - len(unique_ips)} duplicate IP assignments:")
            for ip, count in duplicates[:5]:
                logger.error(f"  IP {ip} assigned {count} times")
            return False
        return True

    def _check_exit_policy_for_web_traffic(self, exit_policy: Dict) -> bool:
        if not exit_policy:
            return False
        accepts = exit_policy.get('accept', [])
        rejects = exit_policy.get('reject', [])
        if accepts:
            for rule in accepts:
                if rule == '443':
                    return True
                if '-' in rule:
                    try:
                        start, end = map(int, rule.split('-'))
                        if 443 >= start and 443 <= end:
                            return True
                    except Exception:
                        pass
            return False
        if not accepts and rejects:
            for rule in rejects:
                if rule == '1-65535':
                    return False
        return False

    def _check_node_stability(self, relay: Dict) -> bool:
        flags = relay.get('flags', [])
        return 'Running' in flags
