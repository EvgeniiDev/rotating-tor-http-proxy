#!/usr/bin/env python3
import os
import logging
import time

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

def main():
    tor_count = int(os.environ.get('TOR_PROCESSES', '50'))
    proxy_port = int(os.environ.get('PROXY_PORT', '8080'))
    
    config_builder = TorConfigBuilder()
    max_test_workers = min(10, tor_count)
    checker = ExitNodeChecker(test_requests_count=6, required_success_count=3, timeout=30, config_builder=config_builder, max_workers=max_test_workers)
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
        print("🧹 Cleaning up resources...")
        try:
            checker.cleanup()
        except Exception as e:
            print(f"Warning: Error during checker cleanup: {e}")
        try:
            manager.stop()
        except Exception as e:
            print(f"Warning: Error during manager stop: {e}")
        print("✅ Pool stopped")

if __name__ == "__main__":
    main()
