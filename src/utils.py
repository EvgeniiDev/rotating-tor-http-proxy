import threading
import time

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
