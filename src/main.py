#!/usr/bin/env python3
import os
import logging
import time
import subprocess
import signal

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

def cleanup_tor_processes():
    """Очищает все существующие процессы Tor перед стартом"""
    print("🧹 Cleaning up existing Tor processes...")
    
    try:
        result = subprocess.run(['pkill', '-f', 'tor'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Killed existing Tor processes")
        else:
            print("ℹ️ No existing Tor processes found")
    except Exception as e:
        print(f"⚠️ Error killing Tor processes: {e}")
    
    try:
        result = subprocess.run(['pkill', '-f', 'python.*tor'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Killed existing Python Tor processes")
    except Exception as e:
        print(f"⚠️ Error killing Python Tor processes: {e}")
    
    try:
        import glob
        config_files = glob.glob('/tmp/torrc_*')
        data_dirs = glob.glob('/tmp/tor_data_*')
        
        for file in config_files + data_dirs:
            try:
                if os.path.isfile(file):
                    os.remove(file)
                elif os.path.isdir(file):
                    subprocess.run(['rm', '-rf', file], check=True)
                print(f"✅ Removed {file}")
            except Exception as e:
                print(f"⚠️ Failed to remove {file}: {e}")
    except Exception as e:
        print(f"⚠️ Error cleaning temp files: {e}")
    
    time.sleep(2)
    print("✅ Cleanup completed")

def main():
    cleanup_tor_processes()
    
    print("Starting Tor HTTP Proxy with new architecture...")
    
    tor_count = int(os.environ.get('TOR_PROCESSES', '20'))
    proxy_port = int(os.environ.get('PROXY_PORT', '8080'))
    
    config_builder = TorConfigBuilder()
    checker = ExitNodeChecker(test_requests_count=6, required_success_count=3, timeout=30, config_builder=config_builder)
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
