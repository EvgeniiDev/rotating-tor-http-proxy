import logging
import threading
import time
from typing import Dict, List, Set
from datetime import datetime, timedelta
from collections import defaultdict
from utils import safe_stop_thread

logger = logging.getLogger(__name__)


class ExitNodeMonitor:
    def __init__(self, inactive_threshold_minutes=60, check_interval_seconds=300):
        self.inactive_threshold = timedelta(minutes=inactive_threshold_minutes)
        self.check_interval = check_interval_seconds
        
        self.active_nodes: Dict[str, datetime] = {}
        self.suspicious_nodes: Set[str] = set()
        self.blacklisted_nodes: Set[str] = set()
        self.node_usage_count: Dict[str, int] = defaultdict(int)
        
        self._lock = threading.RLock()
        self._monitor_thread = None
        self._shutdown_event = threading.Event()
        self.running = False
        
    def start_monitoring(self):
        if self.running:
            return
            
        self.running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            name="ExitNodeMonitor"
        )
        self._monitor_thread.daemon = True
        self._monitor_thread.start()
        logger.info("Exit node monitoring started")
        
    def stop_monitoring(self):
        if not self.running:
            return
            
        self.running = False
        self._shutdown_event.set()
        
        safe_stop_thread(self._monitor_thread)
            
        logger.info("Exit node monitoring stopped")
        
    def report_active_node(self, ip: str):
        with self._lock:
            self.active_nodes[ip] = datetime.now()
            self.node_usage_count[ip] += 1
            
            if ip in self.suspicious_nodes:
                self.suspicious_nodes.remove(ip)
                logger.info(f"Node {ip} recovered from suspicious list")
                
    def get_inactive_nodes(self) -> List[str]:
        with self._lock:
            current_time = datetime.now()
            inactive = []
            
            for ip, last_seen in self.active_nodes.items():
                if current_time - last_seen > self.inactive_threshold:
                    inactive.append(ip)
                    
            return inactive
            
    def get_suspicious_nodes(self) -> List[str]:
        with self._lock:
            return list(self.suspicious_nodes)
            
    def get_blacklisted_nodes(self) -> List[str]:
        with self._lock:
            return list(self.blacklisted_nodes)
            
    def blacklist_node(self, ip: str):
        with self._lock:
            self.blacklisted_nodes.add(ip)
            self.suspicious_nodes.discard(ip)
            if ip in self.active_nodes:
                del self.active_nodes[ip]
            logger.warning(f"Node {ip} blacklisted")
            
    def is_node_healthy(self, ip: str) -> bool:
        with self._lock:
            return ip not in self.blacklisted_nodes and ip not in self.suspicious_nodes
            
    def get_stats(self) -> dict:
        with self._lock:
            current_time = datetime.now()
            active_count = 0
            inactive_count = 0
            
            for ip, last_seen in self.active_nodes.items():
                if current_time - last_seen <= self.inactive_threshold:
                    active_count += 1
                else:
                    inactive_count += 1
                    
            return {
                'total_tracked_nodes': len(self.active_nodes),
                'active_nodes': active_count,
                'inactive_nodes': inactive_count,
                'suspicious_nodes': len(self.suspicious_nodes),
                'blacklisted_nodes': len(self.blacklisted_nodes),
                'most_used_nodes': sorted(
                    self.node_usage_count.items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:10]
            }
            
    def _monitor_loop(self):
        logger.info("Exit node monitor loop started")
        
        while not self._shutdown_event.is_set() and self.running:
            try:
                self._check_inactive_nodes()
                self._shutdown_event.wait(self.check_interval)
            except Exception as e:
                logger.error(f"Error in monitor loop: {e}")
                time.sleep(10)
                
        logger.info("Exit node monitor loop stopped")
        
    def _check_inactive_nodes(self):
        with self._lock:
            current_time = datetime.now()
            newly_suspicious = []
            
            for ip, last_seen in list(self.active_nodes.items()):
                if current_time - last_seen > self.inactive_threshold:
                    if ip not in self.suspicious_nodes:
                        self.suspicious_nodes.add(ip)
                        newly_suspicious.append(ip)
                        
            if newly_suspicious:
                logger.warning(f"Marked {len(newly_suspicious)} nodes as suspicious: {newly_suspicious[:5]}...")


class NodeRedistributor:
    def __init__(self, monitor: ExitNodeMonitor, pool_manager, relay_manager):
        self.monitor = monitor
        self.pool_manager = pool_manager
        self.relay_manager = relay_manager
        
        self._lock = threading.RLock()
        self.available_backup_nodes = []
        
    def refresh_backup_nodes(self):
        try:
            relay_data = self.relay_manager.fetch_tor_relays()
            if relay_data:
                all_nodes = self.relay_manager.extract_relay_ips(relay_data)
                blacklisted = self.monitor.get_blacklisted_nodes()
                suspicious = self.monitor.get_suspicious_nodes()
                
                backup_nodes = [
                    node for node in all_nodes 
                    if node['ip'] not in blacklisted and node['ip'] not in suspicious
                ]
                
                with self._lock:
                    self.available_backup_nodes = backup_nodes
                    
                logger.info(f"Refreshed backup nodes: {len(backup_nodes)} available")
                return True
        except Exception as e:
            logger.error(f"Failed to refresh backup nodes: {e}")
            
        return False
        
    def redistribute_nodes(self) -> bool:
        suspicious_nodes = self.monitor.get_suspicious_nodes()
        if not suspicious_nodes:
            return True
            
        if not self.available_backup_nodes:
            if not self.refresh_backup_nodes():
                return False
                
        replacements_made = 0
        
        with self._lock:
            for instance_port, instance in self.pool_manager.instances.items():
                instance_nodes = instance.exit_nodes.copy()
                needs_replacement = []
                
                for node_ip in instance_nodes:
                    if node_ip in suspicious_nodes:
                        needs_replacement.append(node_ip)
                        
                if needs_replacement and self.available_backup_nodes:
                    new_nodes = instance_nodes.copy()
                    
                    for old_node in needs_replacement:
                        if self.available_backup_nodes:
                            backup_node = self.available_backup_nodes.pop(0)
                            new_nodes = [backup_node['ip'] if n == old_node else n for n in new_nodes]
                            self.monitor.blacklist_node(old_node)
                            replacements_made += 1
                            logger.info(f"Replaced {old_node} with {backup_node['ip']} in instance {instance_port}")
                            
                    if new_nodes != instance_nodes:
                        instance.exit_nodes = new_nodes
                        if instance.is_running:
                            instance.restart()
                            
        if replacements_made > 0:
            logger.info(f"Completed redistribution: {replacements_made} nodes replaced")
            
        return True
