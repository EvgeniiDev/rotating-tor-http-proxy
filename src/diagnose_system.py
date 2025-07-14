#!/usr/bin/env python3

import logging
import subprocess
import sys
import time

from utils import is_port_available, kill_process_on_port
from proxy_manager import ProxyManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def check_system_dependencies():
    """Check if required system dependencies are available."""
    logger.info("=== SYSTEM DEPENDENCIES CHECK ===")
    
    dependencies = ['tor', 'polipo', 'haproxy']
    missing = []
    
    for dep in dependencies:
        try:
            result = subprocess.run([dep, '--version'], 
                                  capture_output=True, timeout=5)
            if result.returncode == 0:
                logger.info(f"✓ {dep} is available")
            else:
                logger.error(f"✗ {dep} returned error code {result.returncode}")
                missing.append(dep)
        except FileNotFoundError:
            logger.error(f"✗ {dep} not found in PATH")
            missing.append(dep)
        except subprocess.TimeoutExpired:
            logger.error(f"✗ {dep} command timed out")
            missing.append(dep)
        except Exception as e:
            logger.error(f"✗ {dep} check failed: {e}")
            missing.append(dep)
    
    if missing:
        logger.error(f"Missing dependencies: {', '.join(missing)}")
        return False
    else:
        logger.info("All dependencies are available")
        return True


def check_port_availability():
    """Check availability of all required ports."""
    logger.info("=== PORT AVAILABILITY CHECK ===")
    
    base_tor_port = 9050
    base_http_port = 8001
    num_proxies = 5
    
    conflicts = []
    
    # Check Tor ports
    for i in range(num_proxies):
        port = base_tor_port + i
        if not is_port_available('127.0.0.1', port):
            conflicts.append(('Tor', port))
    
    # Check HTTP ports
    for i in range(num_proxies):
        port = base_http_port + i
        if not is_port_available('127.0.0.1', port):
            conflicts.append(('HTTP', port))
    
    # Check HAProxy ports
    for service, port in [('HAProxy', 8080), ('HAProxy Stats', 8404)]:
        if not is_port_available('127.0.0.1', port):
            conflicts.append((service, port))
    
    if conflicts:
        logger.warning(f"Found {len(conflicts)} port conflicts:")
        for service, port in conflicts:
            logger.warning(f"  - {service}: port {port}")
        return False
    else:
        logger.info("All required ports are available")
        return True


def check_filesystem_permissions():
    """Check if we can create necessary directories and files."""
    logger.info("=== FILESYSTEM PERMISSIONS CHECK ===")
    
    import os
    import tempfile
    
    test_dirs = [
        '~/tor-http-proxy/tor',
        '~/tor-http-proxy/polipo', 
        '~/tor-http-proxy/haproxy',
        '~/tor-http-proxy/data'
    ]
    
    all_good = True
    
    for test_dir in test_dirs:
        expanded_dir = os.path.expanduser(test_dir)
        try:
            os.makedirs(expanded_dir, exist_ok=True)
            
            # Test file creation
            test_file = os.path.join(expanded_dir, 'test_write.tmp')
            with open(test_file, 'w') as f:
                f.write('test')
            
            # Test file reading
            with open(test_file, 'r') as f:
                content = f.read()
            
            # Cleanup
            os.remove(test_file)
            
            logger.info(f"✓ {test_dir} - read/write OK")
            
        except Exception as e:
            logger.error(f"✗ {test_dir} - failed: {e}")
            all_good = False
    
    return all_good


def test_tor_startup():
    """Test if a single Tor instance can start successfully."""
    logger.info("=== TOR STARTUP TEST ===")
    
    import tempfile
    import os
    
    # Create a minimal Tor config
    with tempfile.NamedTemporaryFile(mode='w', suffix='.conf', delete=False) as f:
        config_content = """
SocksPort 127.0.0.1:19050
DataDirectory /tmp/tor_test_data
ClientOnly 1
UseMicrodescriptors 1
AvoidDiskWrites 1
"""
        f.write(config_content)
        config_file = f.name
    
    try:
        # Start Tor with test config
        logger.info("Starting test Tor instance...")
        tor_process = subprocess.Popen(
            ['tor', '-f', config_file],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # Wait and check
        time.sleep(10)
        
        if tor_process.poll() is None:
            logger.info("✓ Tor started successfully")
            
            # Check if port is bound
            if not is_port_available('127.0.0.1', 19050):
                logger.info("✓ Tor is listening on test port")
                result = True
            else:
                logger.warning("⚠ Tor started but not listening on expected port")
                result = False
            
            # Stop the test instance
            tor_process.terminate()
            tor_process.wait(timeout=10)
            logger.info("✓ Test Tor instance stopped")
        else:
            stdout, stderr = tor_process.communicate()
            logger.error("✗ Tor failed to start")
            logger.error(f"Stdout: {stdout.decode()[:500]}")
            logger.error(f"Stderr: {stderr.decode()[:500]}")
            result = False
    
    except Exception as e:
        logger.error(f"✗ Tor test failed: {e}")
        result = False
    
    finally:
        # Cleanup
        try:
            os.unlink(config_file)
            import shutil
            shutil.rmtree('/tmp/tor_test_data', ignore_errors=True)
        except:
            pass
    
    return result


def diagnose_current_system():
    """Run complete system diagnosis."""
    logger.info("Starting comprehensive system diagnosis...")
    
    checks = [
        ("System Dependencies", check_system_dependencies),
        ("Filesystem Permissions", check_filesystem_permissions),
        ("Port Availability", check_port_availability),
        ("Tor Startup", test_tor_startup),
    ]
    
    results = {}
    
    for check_name, check_func in checks:
        try:
            results[check_name] = check_func()
        except Exception as e:
            logger.error(f"Check '{check_name}' failed with exception: {e}")
            results[check_name] = False
    
    logger.info("=== DIAGNOSIS SUMMARY ===")
    all_passed = True
    
    for check_name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        symbol = "✓" if passed else "✗"
        logger.info(f"{symbol} {check_name}: {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        logger.info("🎉 All checks passed! System should be ready.")
    else:
        logger.warning("⚠ Some checks failed. Address the issues above before running the proxy system.")
    
    return all_passed


def fix_common_issues():
    """Attempt to fix common issues automatically."""
    logger.info("=== ATTEMPTING TO FIX COMMON ISSUES ===")
    
    # Fix port conflicts
    logger.info("Checking and fixing port conflicts...")
    ports_to_check = list(range(9050, 9055)) + list(range(8001, 8006)) + [8080, 8404]
    
    for port in ports_to_check:
        if not is_port_available('127.0.0.1', port):
            logger.info(f"Attempting to free port {port}...")
            if kill_process_on_port(port):
                logger.info(f"✓ Freed port {port}")
            else:
                logger.warning(f"⚠ Could not free port {port}")
    
    # Create directories
    logger.info("Creating necessary directories...")
    import os
    
    dirs_to_create = [
        '~/tor-http-proxy/tor',
        '~/tor-http-proxy/polipo',
        '~/tor-http-proxy/haproxy',
        '~/tor-http-proxy/data'
    ]
    
    for dir_path in dirs_to_create:
        expanded_path = os.path.expanduser(dir_path)
        try:
            os.makedirs(expanded_path, exist_ok=True)
            logger.info(f"✓ Created/verified directory: {expanded_path}")
        except Exception as e:
            logger.error(f"✗ Failed to create directory {expanded_path}: {e}")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Diagnose and fix proxy system issues')
    parser.add_argument('--fix', action='store_true',
                       help='Attempt to automatically fix common issues')
    parser.add_argument('--quick', action='store_true',
                       help='Run only quick checks (skip Tor startup test)')
    
    args = parser.parse_args()
    
    if args.fix:
        fix_common_issues()
        time.sleep(2)  # Wait for changes to take effect
    
    success = diagnose_current_system()
    
    if not success:
        logger.error("System diagnosis failed. See errors above.")
        if not args.fix:
            logger.info("Try running with --fix to automatically resolve common issues.")
        sys.exit(1)
    else:
        logger.info("System diagnosis completed successfully!")
        sys.exit(0)
