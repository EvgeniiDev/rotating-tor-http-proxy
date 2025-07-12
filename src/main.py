#!/usr/bin/env python3
import os
import logging
import time
import socket

from http_load_balancer import HTTPLoadBalancer
from tor_pool_manager import TorBalancerManager
from config_manager import TorConfigBuilder
from tor_parallel_runner import TorParallelRunner
from exit_node_tester import ExitNodeChecker
from tor_relay_manager import TorRelayManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def find_available_port(start_port=8080, max_attempts=50):
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(('0.0.0.0', port))
                return port
        except OSError:
            continue
    return None

def main():
    print("Starting Tor HTTP Proxy with new architecture...")
    
    tor_count = int(os.environ.get('TOR_PROCESSES', '20'))
    desired_port = int(os.environ.get('PROXY_PORT', '8080'))
    
    proxy_port = find_available_port(desired_port)
    if proxy_port is None:
        print(f"❌ No available ports found starting from {desired_port}")
        return
    
    if proxy_port != desired_port:
        print(f"⚠️  Port {desired_port} is in use, using port {proxy_port} instead")
    
    config_builder = TorConfigBuilder()
    checker = ExitNodeChecker(test_requests_count=2, required_success_count=1, timeout=8, config_builder=config_builder)
    runner = TorParallelRunner(config_builder)
    balancer = HTTPLoadBalancer(listen_port=proxy_port)
    manager = TorBalancerManager(config_builder, checker, runner, balancer)
    
    try:
        exit_nodes_env = os.environ.get('EXIT_NODES', '')
        if exit_nodes_env:
            exit_nodes = exit_nodes_env.split(',')
            print(f"Using {len(exit_nodes)} exit nodes from environment")
        else:
            print("Fetching exit nodes from Tor relay manager...")
            relay_manager = TorRelayManager()
            relay_data = relay_manager.fetch_tor_relays()
            if relay_data:
                all_exit_nodes = relay_manager.extract_relay_ips(relay_data)
                max_nodes = tor_count * 6
                limited_nodes = all_exit_nodes[:max_nodes]
                exit_nodes = [node['ip'] for node in limited_nodes]
                print(f"Found {len(all_exit_nodes)} total exit nodes, using {len(exit_nodes)} (limit: {max_nodes})")
        
        print(f"Using {len(exit_nodes)} exit nodes for {tor_count} Tor processes")
        
        print(f"Starting Tor pool with {tor_count} processes...")
        success = manager.run_pool(count=tor_count, exit_nodes=exit_nodes)
        
        if success:
            print("✅ Pool started successfully!")
            print(f"🌐 HTTP proxy is running on http://localhost:{proxy_port}")
        
            while True:
                time.sleep(1)
            
        else:
            print("❌ Failed to start pool")
            
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        checker.cleanup()
        manager.stop()
        print("✅ Pool stopped")

if __name__ == "__main__":
    main()
