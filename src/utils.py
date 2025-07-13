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
        self._threads: List[threading.Thread] = []
        self._shutdown_event = threading.Event()
        self._lock = threading.Lock()
    
    def create_executor(self, max_workers: int, thread_name_prefix: str = "Worker") -> ThreadPoolExecutor:
        with self._lock:
            if self._shutdown_event.is_set():
                raise RuntimeError("ThreadManager is shutting down")
            executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=thread_name_prefix)
            self._executors.append(executor)
            return executor
    
    def register_thread(self, thread: threading.Thread):
        with self._lock:
            if self._shutdown_event.is_set():
                logger.warning("Cannot register thread - manager is shutting down")
                return
            self._threads.append(thread)
    
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
            
            logger.info(f"Stopping {len(self._threads)} individual threads...")
            for thread in self._threads:
                try:
                    if thread.is_alive():
                        thread.join(timeout=timeout//len(self._threads) if self._threads else timeout)
                except Exception as e:
                    logger.error(f"Error stopping thread {thread.name}: {e}")
            
            alive_threads = [t for t in self._threads if t.is_alive()]
            if alive_threads:
                logger.warning(f"{len(alive_threads)} threads still alive after shutdown")
            
            self._executors.clear()
            self._threads.clear()
        
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

# Создаем глобальный реестр потоков для отслеживания
_thread_registry = {}
_registry_lock = threading.Lock()

def register_thread(thread: threading.Thread, category: str = "unknown"):
    """Регистрирует поток для отслеживания"""
    with _registry_lock:
        _thread_registry[thread.ident] = {
            'thread': thread,
            'category': category,
            'name': thread.name,
            'created_at': time.time()
        }

def unregister_thread(thread: threading.Thread):
    """Удаляет поток из реестра"""
    with _registry_lock:
        _thread_registry.pop(thread.ident, None)

def get_thread_count_by_category():
    """Возвращает количество потоков по категориям"""
    with _registry_lock:
        alive_threads = {tid: info for tid, info in _thread_registry.items() 
                        if info['thread'].is_alive()}
        _thread_registry.clear()
        _thread_registry.update(alive_threads)
        
        categories = {}
        for info in _thread_registry.values():
            cat = info['category']
            categories[cat] = categories.get(cat, 0) + 1
        return categories

def cleanup_dead_threads():
    """Очищает мертвые потоки из реестра"""
    with _registry_lock:
        alive_threads = {}
        for tid, info in _thread_registry.items():
            if info['thread'].is_alive():
                alive_threads[tid] = info
        
        cleaned_count = len(_thread_registry) - len(alive_threads)
        _thread_registry.clear()
        _thread_registry.update(alive_threads)
        
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} dead thread references")
        
        return cleaned_count

def emergency_cleanup():
    """Аварийная очистка всех потоков"""
    logger.warning("Emergency thread cleanup initiated")
    
    with _registry_lock:
        for info in _thread_registry.values():
            thread = info['thread']
            if thread.is_alive() and not thread.daemon:
                try:
                    thread.join(timeout=1)
                except Exception as e:
                    logger.error(f"Error during emergency cleanup of thread {thread.name}: {e}")
        
        _thread_registry.clear()
    
    # Принудительная сборка мусора
    gc.collect()

atexit.register(emergency_cleanup)
