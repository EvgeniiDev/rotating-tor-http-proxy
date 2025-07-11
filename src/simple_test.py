#!/usr/bin/env python3
"""
Упрощенный тест новой архитектуры - запускает один Tor процесс и HTTP балансировщик
"""
import logging
import time
from config_manager import TorConfigBuilder
from tor_process import TorInstance
from http_load_balancer import HTTPLoadBalancer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def test_simple_architecture():
    print("🚀 Testing new architecture with simplified setup...")
    
    # 1. Создаём конфиг для Tor
    config_builder = TorConfigBuilder()
    print("✅ Config builder created")
    
    # 2. Создаём HTTP балансировщик
    balancer = HTTPLoadBalancer(listen_port=8080)
    print("✅ HTTP Load Balancer created")
    
    # 3. Создаём один Tor процесс (без exit nodes для простоты)
    tor_instance = TorInstance(port=9050, exit_nodes=[], config_builder=config_builder)
    print("✅ Tor instance created")
    
    try:
        # 4. Создаём конфиг и запускаем Tor
        print("📝 Creating Tor config...")
        tor_instance.create_config()
        
        print("🔄 Starting Tor process...")
        tor_instance.start()
        
        # 5. Ждём запуска Tor
        print("⏳ Waiting for Tor to start...")
        time.sleep(10)
        
        # 6. Проверяем здоровье
        print("🏥 Checking Tor health...")
        if tor_instance.check_health():
            print("✅ Tor is healthy!")
            status = tor_instance.get_status()
            print(f"📊 Status: {status}")
        else:
            print("❌ Tor health check failed")
            return False
        
        # 7. Добавляем в балансировщик и запускаем
        print("⚖️ Adding to load balancer...")
        balancer.add_proxy(9050)
        balancer.start()
        print("✅ HTTP Load Balancer started on port 8080")
        
        print("\n🎉 SUCCESS! Architecture working!")
        print("🌐 Test with: curl -x http://localhost:8080 https://httpbin.org/ip")
        print("⏱️ Running for 30 seconds...")
        
        time.sleep(30)
        return True
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
        
    finally:
        print("🛑 Cleaning up...")
        tor_instance.stop()
        balancer.stop()
        print("✅ Cleanup complete")

if __name__ == "__main__":
    success = test_simple_architecture()
    if success:
        print("🎊 Test PASSED!")
    else:
        print("💥 Test FAILED!")