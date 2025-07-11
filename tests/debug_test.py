#!/usr/bin/env python3
"""
Отладочный тест для диагностики проблем с Tor
"""
import logging
import time
import subprocess
import tempfile
import os
from config_manager import TorConfigBuilder

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

def debug_tor():
    print("🔍 Debugging Tor setup...")
    
    # 1. Создаём конфиг
    config_builder = TorConfigBuilder()
    port = 9050
    
    print(f"📝 Creating config for port {port}...")
    config_content = config_builder.build_config_without_exit_nodes(port)
    print("✅ Config content:")
    print(config_content)
    print()
    
    # 2. Создаём временный файл конфигурации
    temp_fd, config_file = tempfile.mkstemp(suffix='.torrc', prefix=f'debug_tor_{port}_')
    with os.fdopen(temp_fd, 'w') as f:
        f.write(config_content)
    
    print(f"💾 Config saved to: {config_file}")
    
    try:
        # 3. Запускаем Tor с логами
        print("🚀 Starting Tor with logs...")
        cmd = ['tor', '-f', config_file]
        print(f"Command: {' '.join(cmd)}")
        
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        
        print("⏳ Waiting for Tor to start (20 seconds)...")
        time.sleep(20)
        
        # 4. Проверяем статус процесса
        if process.poll() is None:
            print("✅ Tor process is running")
        else:
            print(f"❌ Tor process died with code: {process.returncode}")
            print("📜 Process output:")
            output, _ = process.communicate()
            print(output)
            return False
        
        # 5. Проверяем соединение
        print("🌐 Testing connection...")
        import requests
        proxies = {'http': f'socks5://127.0.0.1:{port}', 'https': f'socks5://127.0.0.1:{port}'}
        
        try:
            response = requests.get('https://api.ipify.org?format=json', proxies=proxies, timeout=15)
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Connection successful! Exit IP: {result.get('ip')}")
                return True
            else:
                print(f"❌ HTTP error: {response.status_code}")
        except requests.RequestException as e:
            print(f"❌ Connection failed: {e}")
        
        return False
        
    finally:
        print("🛑 Stopping Tor...")
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        
        # Удаляем временный файл
        if os.path.exists(config_file):
            os.unlink(config_file)
        
        print("✅ Cleanup complete")

if __name__ == "__main__":
    success = debug_tor()
    if success:
        print("🎊 Tor works correctly!")
    else:
        print("💥 Tor debugging failed!")