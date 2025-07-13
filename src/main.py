#!/usr/bin/env python3
import os
import logging
import time
import signal
import sys

from http_load_balancer import HTTPLoadBalancer
from tor_pool_manager import TorBalancerManager
from config_manager import TorConfigBuilder
from tor_parallel_runner import TorParallelRunner
from exit_node_tester import ExitNodeChecker
from tor_relay_manager import TorRelayManager
from utils import thread_manager, cleanup_temp_files, get_thread_count_by_category, cleanup_dead_threads

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

shutdown_requested = False

def signal_handler(signum, frame):
    global shutdown_requested
    print(f"\n🛑 Received signal {signum}, shutting down...")
    shutdown_requested = True

def main():
    global shutdown_requested
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
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
            
            # Мониторинг потоков
            monitor_interval = 30
            last_monitor = 0
        
            while not shutdown_requested:
                current_time = time.time()
                if current_time - last_monitor >= monitor_interval:
                    cleanup_dead_threads()
                    thread_counts = get_thread_count_by_category()
                    if thread_counts:
                        print(f"📊 Thread status: {thread_counts}")
                    last_monitor = current_time
                
                time.sleep(1)
            
        else:
            print("❌ Failed to start pool")
            
    except KeyboardInterrupt:
        print("\n🛑 Keyboard interrupt received...")
        shutdown_requested = True
    except Exception as e:
        print(f"❌ Error: {e}")
        shutdown_requested = True
    finally:
        print("🧹 Cleaning up resources...")
        cleanup_start = time.time()
        
        try:
            if 'checker' in locals():
                checker.cleanup()
        except Exception as e:
            print(f"Warning: Error during checker cleanup: {e}")
        
        try:
            if 'manager' in locals():
                manager.stop()
        except Exception as e:
            print(f"Warning: Error during manager stop: {e}")
        
        try:
            thread_manager.shutdown_all(timeout=30)
        except Exception as e:
            print(f"Warning: Error during thread manager shutdown: {e}")
        
        cleanup_temp_files()
        
        cleanup_time = time.time() - cleanup_start
        print(f"✅ Pool stopped (cleanup took {cleanup_time:.1f}s)")
        
        sys.exit(0)

if __name__ == "__main__":
    main()
