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


