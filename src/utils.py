import atexit
import signal
import threading
import gc
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

logger = logging.getLogger(__name__)

class ThreadManager:
    """
    Централизованное управление потоками для предотвращения утечек.
    """
    def __init__(self):
        self._executors: List[ThreadPoolExecutor] = []
        self._shutdown_event = threading.Event()
        self._lock = threading.Lock()
    
    def create_executor(self, max_workers: int, thread_name_prefix: str = "Worker") -> ThreadPoolExecutor:
        with self._lock:
            if self._shutdown_event.is_set():
                raise RuntimeError("ThreadManager is shutting down")
            executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
            self._executors.append(executor)
            return executor
    
    def shutdown_all(self, timeout: int = 30):
        logger.info("Shutting down ThreadManager...")
        self._shutdown_event.set()
        
        with self._lock:
            logger.info(f"Shutting down {len(self._executors)} thread pools...")
            for executor in self._executors:
                try:
                    executor.shutdown(wait=False)
                except Exception as e:
                    logger.error(f"Error shutting down executor: {e}")
            
            for executor in self._executors:
                try:
                    if not executor._shutdown:
                        executor.shutdown(wait=True)
                except Exception as e:
                    logger.error(f"Error waiting for executor shutdown: {e}")
            
            self._executors.clear()
        
        logger.info("ThreadManager shutdown complete")

thread_manager = ThreadManager()

def is_valid_ipv4(ip: str) -> bool:
    parts = ip.split('.')
    if len(parts) != 4:
        return False
    for part in parts:
        if not part.isdigit():
            return False
        if not 0 <= int(part) <= 255:
            return False
    return True

def safe_stop_thread(thread: threading.Thread, timeout: int = 10):
    if thread and thread.is_alive():
        thread.join(timeout=timeout)

def safe_thread_wait(shutdown_event, interval, running_condition=None):
    while not shutdown_event.is_set():
        if running_condition and not running_condition():
            break
        shutdown_event.wait(interval)

def cleanup_temp_files():
    import glob
    import os
    temp_files = glob.glob("/tmp/tor_*")
    cleaned = 0
    for temp_file in temp_files:
        try:
            if os.path.isfile(temp_file):
                os.unlink(temp_file)
                cleaned += 1
            elif os.path.isdir(temp_file):
                import shutil
                shutil.rmtree(temp_file, ignore_errors=True)
                cleaned += 1
        except Exception:
            pass
    if cleaned > 0:
        logger.info(f"Cleaned up {cleaned} temporary files")
    return cleaned

