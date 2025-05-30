import requests
import re
from collections import defaultdict

def fetch_tor_relays():
    """Fetch Tor relay information from Onionoo API"""
    url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses"
    response = requests.get(url)
    if response.status_code != 200:
        raise Exception(f"Failed to fetch data: HTTP {response.status_code}")
    return response.json()

def extract_ipv4_addresses(relays_data):
    """Extract IPv4 addresses from relay data"""
    ipv4_addresses = []
    
    for relay in relays_data["relays"]:
        for address in relay.get("or_addresses", []):
            # Skip IPv6 addresses (in square brackets)
            if "[" in address:
                continue
                
            # Extract IP from IP:PORT format
            match = re.match(r"(\d+\.\d+\.\d+\.\d+):\d+", address)
            if match:
                ipv4_addresses.append(match.group(1))
    
    return ipv4_addresses

def group_by_subnet(ip_addresses):
    """Group IP addresses by /16 subnet"""
    subnet_groups = defaultdict(list)
    
    for ip in ip_addresses:
        # Get first two octets for /16 subnet
        octets = ip.split(".")
        subnet = f"{octets[0]}.{octets[1]}"
        subnet_groups[subnet].append(ip)
    
    return subnet_groups

def main():
    print("Fetching Tor relay data...")
    relay_data = fetch_tor_relays()
    
    print("Extracting IPv4 addresses...")
    ip_addresses = extract_ipv4_addresses(relay_data)
    print(f"Found {len(ip_addresses)} IPv4 addresses")
    
    print("Grouping by subnet...")
    subnet_groups = group_by_subnet(ip_addresses)
    
    # Filter subnets with at least 10 addresses
    significant_subnets = {subnet: addresses for subnet, addresses in subnet_groups.items() 
                          if len(addresses) >= 10}
    
    print(f"\n=== Subnet Distribution (/16) with 10+ addresses ===")
    print(f"Showing {len(significant_subnets)} out of {len(subnet_groups)} subnets")
    
    # Sort by number of addresses (descending)
    sorted_subnets = sorted(significant_subnets.items(), key=lambda x: len(x[1]), reverse=True)
    
    for subnet, addresses in sorted_subnets:
        print(f"{subnet}.0.0/16: {len(addresses)} addresses")

if __name__ == "__main__":
    main()