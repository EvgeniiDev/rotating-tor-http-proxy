import logging
import subprocess
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class PolipoManager:
    def __init__(self):
        self.base_http_port = 20000  # HTTP ports start from 20000 (socks + 10000)
        self.polipo_processes = {}
        self.running_instances = {}
        self._lock = threading.Lock()

    def get_http_port_for_socks_port(self, socks_port: int) -> int:
        """Calculate HTTP port based on SOCKS port (difference of 10000)"""
        return socks_port + 10000

    def start_polipo_instance(self, instance_id: int, socks_port: int) -> Optional[int]:
        """Start a Polipo instance for HTTP to SOCKS5 conversion"""
        http_port = self.get_http_port_for_socks_port(socks_port)
        
        with self._lock:
            if instance_id in self.polipo_processes:
                process = self.polipo_processes[instance_id]
                if process and process.poll() is None:
                    logger.info(f"Polipo instance {instance_id} already running on port {http_port}")
                    return http_port

            try:
                # Polipo command for HTTP to SOCKS5 conversion
                cmd = [
                    'polipo',
                    f'proxyAddress=0.0.0.0',
                    f'proxyPort={http_port}',
                    f'socksParentProxy=127.0.0.1:{socks_port}',
                    'socksProxyType=socks5',
                    'diskCacheRoot=""',
                    'dontCacheCookies=true',
                    'disableIndexing=true',
                    'logLevel=1',  # Minimal logging
                    'daemonise=false'
                ]

                logger.info(f"Starting Polipo instance {instance_id}: HTTP port {http_port} -> SOCKS port {socks_port}")
                
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    universal_newlines=True
                )

                self.polipo_processes[instance_id] = process
                self.running_instances[instance_id] = {
                    'http_port': http_port,
                    'socks_port': socks_port,
                    'started_at': time.time()
                }

                # Give Polipo a moment to start
                time.sleep(1)
                
                if process.poll() is None:
                    logger.info(f"Polipo instance {instance_id} started successfully on HTTP port {http_port}")
                    return http_port
                else:
                    stderr = process.stderr.read() if process.stderr else "No error output"
                    logger.error(f"Polipo instance {instance_id} failed to start: {stderr}")
                    self._cleanup_instance(instance_id)
                    return None

            except Exception as e:
                logger.error(f"Error starting Polipo instance {instance_id}: {e}")
                self._cleanup_instance(instance_id)
                return None

    def stop_polipo_instance(self, instance_id: int) -> bool:
        """Stop a specific Polipo instance"""
        with self._lock:
            if instance_id not in self.polipo_processes:
                logger.warning(f"Polipo instance {instance_id} not found")
                return False

            try:
                process = self.polipo_processes[instance_id]
                if process and process.poll() is None:
                    process.terminate()
                    
                    # Wait for graceful shutdown
                    try:
                        process.wait(timeout=5)
                        logger.info(f"Polipo instance {instance_id} stopped gracefully")
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                        logger.warning(f"Polipo instance {instance_id} force killed")

                self._cleanup_instance(instance_id)
                return True

            except Exception as e:
                logger.error(f"Error stopping Polipo instance {instance_id}: {e}")
                self._cleanup_instance(instance_id)
                return False

    def stop_all_instances(self):
        """Stop all Polipo instances"""
        with self._lock:
            instance_ids = list(self.polipo_processes.keys())
            
        for instance_id in instance_ids:
            self.stop_polipo_instance(instance_id)

        logger.info("All Polipo instances stopped")

    def get_running_instances(self) -> Dict:
        """Get information about running Polipo instances"""
        with self._lock:
            running = {}
            to_remove = []

            for instance_id, process in self.polipo_processes.items():
                if process and process.poll() is None:
                    if instance_id in self.running_instances:
                        running[instance_id] = self.running_instances[instance_id].copy()
                else:
                    to_remove.append(instance_id)

            # Clean up dead processes
            for instance_id in to_remove:
                self._cleanup_instance(instance_id)

            return running

    def is_instance_running(self, instance_id: int) -> bool:
        """Check if a specific Polipo instance is running"""
        with self._lock:
            if instance_id not in self.polipo_processes:
                return False
            
            process = self.polipo_processes[instance_id]
            is_running = process and process.poll() is None
            
            if not is_running:
                self._cleanup_instance(instance_id)
            
            return is_running

    def _cleanup_instance(self, instance_id: int):
        """Clean up instance data (must be called with lock held)"""
        self.polipo_processes.pop(instance_id, None)
        self.running_instances.pop(instance_id, None)

    def get_stats(self) -> Dict:
        """Get statistics about Polipo instances"""
        running_instances = self.get_running_instances()
        
        return {
            'total_instances': len(running_instances),
            'running_instances': len(running_instances),
            'instance_details': running_instances
        }
