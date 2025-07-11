#!/usr/bin/env python3
import os
import sys
import logging
import signal
import time
import threading

from http_load_balancer import HTTPLoadBalancer
from tor_pool_manager import TorBalancerManager
from config_manager import TorConfigBuilder
from parallel_worker_manager import TorParallelRunner
from exit_node_tester import ExitNodeChecker
from tor_relay_manager import TorRelayManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def main():
    print("Starting Tor HTTP Proxy with new architecture...")
    
    # Создаём компоненты новой архитектуры
    config_builder = TorConfigBuilder()
    checker = ExitNodeChecker(test_requests_count=3, required_success_count=2, timeout=10)  # Упрощаем тесты
    runner = TorParallelRunner(config_builder)
    balancer = HTTPLoadBalancer(listen_port=8080)
    manager = TorBalancerManager(config_builder, checker, runner, balancer)
    
    try:
        # Используем fallback exit-ноды для быстрого тестирования
        print("Using fallback exit nodes for testing...")
        exit_nodes = [
            "185.220.100.240",
            "185.220.100.241", 
            "185.220.100.242",
            "95.216.143.131",
            "185.220.102.4"
        ]
        print(f"Testing {len(exit_nodes)} exit nodes")
        
        # Запускаем пул с 2 процессами для тестирования
        print("Starting Tor pool with 2 processes...")
        success = manager.run_pool(count=2, exit_nodes=exit_nodes)
        
        if success:
            print("✅ Pool started successfully!")
            print(f"🌐 HTTP proxy is running on http://localhost:8080")
            
            # Получаем статистику
            stats = manager.get_stats()
            print(f"📊 Pool stats: {stats}")
            
            print("🔄 Proxy is ready! Test with:")
            print("   curl -x http://localhost:8080 https://httpbin.org/ip")
            print("")
            print("⏱️  Running for 30 seconds... (Press Ctrl+C to stop)")
            time.sleep(30)
            
        else:
            print("❌ Failed to start pool")
            
    except KeyboardInterrupt:
        print("\n🛑 Shutting down...")
    except Exception as e:
        print(f"❌ Error: {e}")
    finally:
        manager.stop()
        print("✅ Pool stopped")

if __name__ == "__main__":
    main()
