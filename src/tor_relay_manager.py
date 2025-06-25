import logging
import requests
from collections import defaultdict

logger = logging.getLogger(__name__)


class TorRelayManager:
    def __init__(self):
        self.current_relays = {}
        self.available_subnets = []
    
    def fetch_tor_relays(self):
        try:
            url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,exit_probability"
            response = requests.get(url, timeout=30)
            return response.json() if response.status_code == 200 else None
        except Exception as e:
            logger.error(f"Error fetching Tor relays: {e}")
            return None
    
    def extract_relay_ips(self, relay_data):
        relays = []
        if not relay_data or 'relays' not in relay_data:
            return relays

        subnet_relays = defaultdict(list)

        for relay in relay_data['relays']:
            if 'or_addresses' in relay:
                for addr in relay['or_addresses']:
                    ip = addr.split(':')[0]
                    if ':' not in ip:
                        ip_parts = ip.split('.')
                        if len(ip_parts) >= 2:
                            subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                            exit_prob = relay.get('exit_probability', 0)
                            
                            if isinstance(exit_prob, str):
                                try:
                                    exit_prob = float(exit_prob)
                                except (ValueError, TypeError):
                                    exit_prob = 0

                            relay_info = {
                                'ip': ip,
                                'country': relay.get('country', 'Unknown'),
                                'exit_probability': exit_prob
                            }
                            subnet_relays[subnet].append(relay_info)

        valid_subnets = set()
        for subnet, subnet_relay_list in subnet_relays.items():
            if any(relay['exit_probability'] > 0 for relay in subnet_relay_list):
                valid_subnets.add(subnet)

        for subnet in valid_subnets:
            relays.extend(subnet_relays[subnet])

        logger.info(f"Filtered to {len(valid_subnets)} subnets with exit probability > 0")
        
        self.available_subnets = sorted(list(valid_subnets))
        self.current_relays = relays
        
        return relays
    
    def get_available_subnets(self, count=None):
        if count is None:
            return self.available_subnets
        return self.available_subnets[:count]
    
    def get_subnet_details(self):
        subnet_counts = defaultdict(int)
        subnet_details = defaultdict(list)

        for relay in self.current_relays:
            ip_parts = relay['ip'].split('.')
            if len(ip_parts) >= 2:
                subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                subnet_counts[subnet] += 1
                subnet_details[subnet].append({
                    'ip': relay['ip'],
                    'country': relay['country'],
                    'exit_probability': relay['exit_probability']
                })

        return subnet_counts, subnet_details
