import re
import socket
import subprocess
import logging
import os

logger = logging.getLogger(__name__)


def is_valid_ipv4(ip: str) -> bool:
    """Check if the given string is a valid IPv4 address."""
    try:
        parts = ip.split('.')
        if len(parts) != 4:
            return False
        for part in parts:
            if not (0 <= int(part) <= 255):
                return False
        return True
    except (ValueError, AttributeError):
        return False


def is_port_available(host: str, port: int) -> bool:
    """Check if a port is available on the given host."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            result = sock.connect_ex((host, port))
            return result != 0  # 0 means port is in use
    except socket.error:
        return False


def find_available_port(host: str, start_port: int, max_attempts: int = 100) -> int:
    """Find an available port starting from start_port."""
    for i in range(max_attempts):
        port = start_port + i
        if is_port_available(host, port):
            return port
    raise RuntimeError(
        f"Could not find available port starting from {start_port}")


def kill_process_on_port(port: int) -> bool:
    """Kill any process using the specified port."""
    try:
        result = subprocess.run(['lsof', '-ti', f':{port}'],
                                capture_output=True, text=True, timeout=10)
        if result.stdout.strip():
            pids = result.stdout.strip().split('\n')
            for pid in pids:
                try:
                    subprocess.run(['kill', '-9', pid],
                                   capture_output=True, timeout=5)
                    logger.info(f"Killed process {pid} using port {port}")
                    return True
                except:
                    pass
    except Exception as e:
        logger.warning(f"Failed to kill process on port {port}: {e}")

    return False


def ensure_port_available(host: str, port: int, force_kill: bool = False) -> bool:
    """Ensure a port is available, optionally killing existing processes."""
    if is_port_available(host, port):
        return True

    if force_kill:
        logger.warning(f"Port {port} is in use, attempting to free it...")
        if kill_process_on_port(port):
            # Wait a moment for the port to be freed
            import time
            time.sleep(2)
            return is_port_available(host, port)

    return False


def setup_haproxy_logging():
    """Setup HAProxy logging for Linux systems using rsyslog."""
    try:
        rsyslog_config = """
# HAProxy logging
$ModLoad imudp
$UDPServerRun 514
$UDPServerAddress 127.0.0.1
local0.*    /var/log/haproxy.log
& stop
"""

        config_file = "/etc/rsyslog.d/49-haproxy.conf"

        result = subprocess.run(['sudo', 'test', '-f', config_file],
                                capture_output=True, timeout=5)

        if result.returncode != 0:
            logger.info("Setting up HAProxy logging configuration...")
            with subprocess.Popen(['sudo', 'tee', config_file],
                                  stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,
                                  text=True) as proc:
                proc.communicate(input=rsyslog_config)

            subprocess.run(['sudo', 'systemctl', 'restart', 'rsyslog'],
                           capture_output=True, timeout=10)
            logger.info("HAProxy logging configured")
        else:
            logger.debug("HAProxy logging already configured")

    except Exception as e:
        logger.warning(f"Could not setup HAProxy logging: {e}")


def check_haproxy_logs():
    """Check recent HAProxy logs for errors."""
    try:
        result = subprocess.run(['sudo', 'tail', '-50', '/var/log/haproxy.log'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout:
            return result.stdout

        result = subprocess.run(['journalctl', '-u', 'haproxy', '--no-pager', '-n', '50'],
                                capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout:
            return result.stdout

    except Exception as e:
        logger.warning(f"Could not check HAProxy logs: {e}")

    return None


def get_process_info(pid: int) -> str:
    """Get detailed process information for debugging."""
    try:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'pid,ppid,cmd,stat,etime'],
                                capture_output=True, text=True, timeout=5)
        return result.stdout if result.returncode == 0 else f"Process {pid} not found"
    except Exception as e:
        return f"Error getting process info: {e}"
