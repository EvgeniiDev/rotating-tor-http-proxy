#!/usr/bin/env python3
"""
Упрощенный тест новой HAProxy архитектуры
"""
import logging
import time
import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))
from haproxy_tor_pool_manager import HAProxyTorPoolManager

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def test_haproxy_architecture():
    print("🚀 Testing HAProxy architecture...")
    
    pool_manager = HAProxyTorPoolManager(frontend_port=8090, stats_port=8404)
    print("✅ HAProxy Tor Pool Manager created")
    
    try:
        print("⏳ Starting pool with 2 processes...")
        success = pool_manager.start_pool(tor_count=2, exit_nodes=[])
        
        if not success:
            print("❌ Failed to start pool")
            return False
            
        print("✅ Pool started successfully")
        time.sleep(20)  # Wait for initialization
        
        stats = pool_manager.get_stats()
        print(f"📊 Running processes: {stats['tor_processes_running']}")
        
        if stats['tor_processes_running'] > 0:
            print(f"🌐 SOCKS5 proxy: 127.0.0.1:{stats['frontend_port']}")
            print("🎉 Test completed successfully!")
            return True
        else:
            print("❌ No processes running")
            return False
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        return False
    finally:
        pool_manager.stop_pool()

if __name__ == "__main__":
    success = test_haproxy_architecture()
    sys.exit(0 if success else 1)
