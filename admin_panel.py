#!/usr/bin/env python3
import os
import json
import time
import signal
import logging
import threading
import subprocess
from datetime import datetime
from collections import defaultdict
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import requests
import re

# Import our config manager
from config_manager import ConfigManager

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tor-admin-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')


class TorNetworkManager:
    def __init__(self):
        self.active_subnets = set()
        self.subnet_limits = {}  # subnet -> max_addresses
        self.tor_instances = []
        self.monitoring = True
        self.current_relays = {}
        self.services_started = False
        self.tor_processes = {}
        self.privoxy_processes = {}
        self.subnet_tor_processes = {}  # subnet -> {instance_id: process}
        self.subnet_privoxy_processes = {}  # subnet -> {instance_id: process}
        self.config_manager = ConfigManager()  # Initialize config manager
        self.stats = {
            'active_subnets': 0,
            'blocked_subnets': 0,
            'last_update': None,
            'tor_instances': 0,
            'running_instances': 0
        }

    def fetch_tor_relays(self):
        """Fetch current Tor relay information"""
        try:
            url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,as_name"
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.json()
        except Exception as e:
            logger.error(f"Error fetching Tor relays: {e}")
        return None

    def extract_relay_ips(self, relay_data):
        """Extract IP addresses from relay data"""
        relays = []
        if not relay_data or 'relays' not in relay_data:
            return relays

        for relay in relay_data['relays']:
            if 'or_addresses' in relay:
                for addr in relay['or_addresses']:
                    ip = addr.split(':')[0]
                    if ':' not in ip:  # IPv4 only
                        relays.append({
                            'ip': ip,
                            'country': relay.get('country', 'Unknown'),
                            'as_name': relay.get('as_name', 'Unknown')
                        })
        return relays



    def start_services(self):
        """Initialize infrastructure without starting Tor instances. Tor instances will be started on user request."""
        if self.services_started:
            logger.info("Services infrastructure already initialized")
            return True

        try:
            logger.info("Initializing services infrastructure...")
            
            # Directories and permissions are already set up by Dockerfile
            # Just verify that HAProxy is accessible for runtime management
            if not self.config_manager.haproxy_manager.is_running():
                logger.warning("HAProxy is not running. It should be started by the shell script.")
            
            # Initialize HAProxy for runtime management (HAProxy is started by shell script)
            self.config_manager.haproxy_manager.initialize_for_runtime_management()

            self.services_started = True
            self.stats['tor_instances'] = 0  # No instances started yet
            self.stats['running_instances'] = 0

            logger.info("Services infrastructure initialized successfully. HAProxy is managed by shell script. Tor instances can now be started on demand.")
            return True

        except Exception as e:
            logger.error(f"Error initializing services infrastructure: {e}")
            return False

    def stop_services(self):
        """Stop all Tor and Privoxy instances"""
        try:
            # Stop Tor processes
            for i, process in self.tor_processes.items():
                if process and process.poll() is None:
                    process.terminate()
                    logger.info(f"Stopped Tor instance {i}")

            # Stop Privoxy processes
            for i, process in self.privoxy_processes.items():
                if process and process.poll() is None:
                    process.terminate()
                    logger.info(f"Stopped Privoxy instance {i}")

            # Stop subnet processes
            for subnet_key, processes in self.subnet_tor_processes.items():
                for instance_id, process in processes.items():
                    if process and process.poll() is None:
                        process.terminate()
                        logger.info(f"Stopped subnet Tor instance {instance_id}")

            for subnet_key, processes in self.subnet_privoxy_processes.items():
                for instance_id, process in processes.items():
                    if process and process.poll() is None:
                        process.terminate()
                        logger.info(f"Stopped subnet Privoxy instance {instance_id}")

            self.tor_processes.clear()
            self.privoxy_processes.clear()
            self.subnet_tor_processes.clear()
            self.subnet_privoxy_processes.clear()
            self.active_subnets.clear()
            
            # HAProxy stop is handled by shell script
            
            self.services_started = False
            self.stats['running_instances'] = 0

            logger.info("All services stopped")
            return True

        except Exception as e:
            logger.error(f"Error stopping services: {e}")
            return False

    def get_service_status(self):
        """Get current service status with detailed information"""
        running_tor = 0
        running_privoxy = 0
        failed_instances = []

        # Check main Tor processes
        for instance_id, process in self.tor_processes.items():
            if process and process.poll() is None:
                running_tor += 1
            elif process and process.poll() is not None:
                failed_instances.append(f"tor-{instance_id}")

        # Check main Privoxy processes
        for instance_id, process in self.privoxy_processes.items():
            if process and process.poll() is None:
                running_privoxy += 1
            elif process and process.poll() is not None:
                failed_instances.append(f"privoxy-{instance_id}")

        # Check subnet-specific processes
        subnet_running_tor = 0
        subnet_running_privoxy = 0

        for subnet_key, processes in self.subnet_tor_processes.items():
            for instance_id, process in processes.items():
                if process and process.poll() is None:
                    subnet_running_tor += 1
                elif process and process.poll() is not None:
                    failed_instances.append(f"subnet-tor-{instance_id}")

        for subnet_key, processes in self.subnet_privoxy_processes.items():
            for instance_id, process in processes.items():
                if process and process.poll() is None:
                    subnet_running_privoxy += 1
                elif process and process.poll() is not None:
                    failed_instances.append(f"subnet-privoxy-{instance_id}")

        total_running_tor = running_tor + subnet_running_tor
        total_running_privoxy = running_privoxy + subnet_running_privoxy

        # Check HAProxy status
        haproxy_running = self.config_manager.haproxy_manager.is_running()

        # Determine overall status
        if self.services_started and total_running_tor > 0 and total_running_privoxy > 0 and haproxy_running:
            if total_running_tor == self.stats.get('tor_instances', 0) and total_running_privoxy == self.stats.get('tor_instances', 0):
                status = "running"
            else:
                status = "partial"
        elif self.services_started or len(self.active_subnets) > 0:
            status = "starting"
        else:
            status = "stopped"

        return {
            'status': status,
            'services_started': self.services_started,
            'running_tor': total_running_tor,
            'running_privoxy': total_running_privoxy,
            'haproxy_running': haproxy_running,
            'total_instances': self.stats.get('tor_instances', 0),
            'failed_instances': failed_instances,
            'active_subnets': len(self.active_subnets),
            'subnet_list': list(self.active_subnets),
            'stats': self.stats.copy(),
            'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }

    def update_subnet_stats(self):
        """Update subnet statistics from current relay data"""
        try:
            relay_data = self.fetch_tor_relays()
            if relay_data and 'relays' in relay_data:                # Instead, just update general stats
                self.stats.update({
                    # Count of actually running subnets
                    'active_subnets': len(self.active_subnets),
                    'last_update': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })

                # Prepare and emit subnet data for the frontend
                self.emit_subnet_data(relay_data['relays'])

                logger.info(
                    f"Updated subnet stats: {len(self.active_subnets)} active subnets")
                return True
            else:
                logger.error("No relay data received or invalid format")
                return False
        except Exception as e:
            logger.error(f"Error updating subnet stats: {e}")
            return False

    def emit_subnet_data(self, relays):
        """Prepare subnet data and emit it via WebSocket"""
        try:
            # Process relays by subnet
            subnet_data = {}

            for relay in relays:
                if 'or_addresses' in relay:
                    for addr in relay['or_addresses']:
                        ip = addr.split(':')[0]
                        if '.' in ip:  # IPv4
                            subnet_prefix = '.'.join(ip.split('.')[:2])
                            subnet = f"{subnet_prefix}.0.0/16"

                            if subnet not in subnet_data:
                                # Check if this subnet is active
                                is_subnet_active = subnet in self.active_subnets
                                subnet_data[subnet] = {
                                    'subnet': subnet_prefix,
                                    'total_relays': 0,
                                    'active_relays': 0,
                                    'countries': set(),
                                    'status': 'running' if is_subnet_active else 'stopped',
                                    'is_active': is_subnet_active,
                                    'limit': 0,
                                    'running_instances': 1 if is_subnet_active else 0,
                                    'instances_count': 1  # Add field required by client
                                }

                            subnet_data[subnet]['total_relays'] += 1
                            subnet_data[subnet]['active_relays'] += 1

                            if 'country' in relay and relay['country']:
                                subnet_data[subnet]['countries'].add(
                                    relay['country'])

            # Convert to list and prepare for JSON serialization
            subnet_list = []
            for subnet, data in subnet_data.items():
                subnet_entry = data.copy()
                subnet_entry['countries'] = list(data['countries'])
                subnet_list.append(subnet_entry)

            # Sort by number of relays
            subnet_list.sort(key=lambda x: x['total_relays'], reverse=True)

            # Limit to top 300 subnets for better performance
            subnet_list = subnet_list[:300]

            # Add debug log
            logger.info(
                f"Preparing to emit subnet data: {len(subnet_list)} subnets")

            # Emit via WebSocket
            socketio.emit('subnet_update', {
                'subnets': subnet_list,
                'stats': self.stats
            })

            logger.info(f"Emitted subnet data: {len(subnet_list)} subnets")
            return True
        except Exception as e:
            logger.error(f"Error preparing subnet data: {e}", exc_info=True)
            # Try to emit an error message so the client knows something went wrong
            socketio.emit('error', {
                'message': f"Error preparing subnet data: {str(e)}"
            })
            return False

    def start_monitoring(self):
        """Start real-time monitoring thread"""
        def monitor_loop():
            last_subnet_update = 0
            while self.monitoring:
                try:
                    current_time = time.time()

                    # Update subnet statistics every 30 seconds
                    if current_time - last_subnet_update >= 30:
                        self.update_subnet_stats()
                        last_subnet_update = current_time

                    # Get current status
                    status = self.get_service_status()

                    # Emit status update via WebSocket
                    socketio.emit('status_update', status)

                    # Sleep for 5 seconds between updates
                    time.sleep(5)

                except Exception as e:
                    logger.error(f"Error in monitoring loop: {e}")
                    time.sleep(10)

        if not hasattr(self, '_monitor_thread') or not self._monitor_thread.is_alive():
            self._monitor_thread = threading.Thread(
                target=monitor_loop, daemon=True)
            self._monitor_thread.start()
            logger.info("Started real-time monitoring")

    def stop_monitoring(self):
        """Stop real-time monitoring"""
        self.monitoring = False
        logger.info("Stopped real-time monitoring")

    def start_subnet_tor(self, subnet, instances_count=1):
        """Start Tor instances for a specific subnet using ConfigManager with subnet-based ExitNodes"""
        try:
            # Validate subnet format
            if not self.config_manager.validate_subnet(subnet):
                logger.error(f"Invalid subnet format: {subnet}")
                return False
               # Ensure log directory exists
            os.makedirs('/var/log/privoxy', exist_ok=True)
            os.makedirs('/var/local/tor', exist_ok=True)

            logger.info(
                f"Starting {instances_count} Tor instances for subnet {subnet}")

            subnet_key = f"{subnet}.0.0/16"
            subnet_cidr = f"{subnet}.0.0/16"

            if subnet_key not in self.subnet_tor_processes:
                self.subnet_tor_processes[subnet_key] = {}
                self.subnet_privoxy_processes[subnet_key] = {}
            
            # Collect instance information for HAProxy config
            subnet_instances = []

            for i in range(1, instances_count + 1):
                instance_id = f"{subnet}_{i}"
                numeric_id = i  # Use numeric ID for port calculation

                # Create instance directory
                instance_dir = f'/var/local/tor/subnet_{subnet}_{i}'
                os.makedirs(instance_dir, exist_ok=True)
                os.chmod(instance_dir, 0o700)                # Get ports for this subnet instance (using numeric ID)
                ports = self.config_manager.get_port_assignment(numeric_id)                # Create Tor configuration using ConfigManager
                tor_config_path = self.config_manager.create_tor_config(
                    instance_id=numeric_id,
                    subnet=subnet
                )

                logger.info(
                    f"Starting Tor instance {instance_id} for subnet {subnet} on ports {ports['socks_port']}/{ports['http_port']} with config {tor_config_path}")

                tor_cmd = ['tor', '-f', tor_config_path]
                process = subprocess.Popen(
                    tor_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                self.subnet_tor_processes[subnet_key][instance_id] = process                
                privoxy_config_path = self.config_manager.create_privoxy_config(
                    instance_id=numeric_id,
                    tor_socks_port=ports['socks_port']
                )

                # Start Privoxy instance
                privoxy_cmd = ['privoxy', '--no-daemon', privoxy_config_path]
                process = subprocess.Popen(
                    privoxy_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE
                )
                self.subnet_privoxy_processes[subnet_key][instance_id] = process

                logger.info(
                    f"Started Tor+Privoxy for subnet {subnet}, instance {i} (targeting {subnet_cidr}) on ports {ports['socks_port']}/{ports['http_port']}")

                # Add to subnet instances list
                subnet_instances.append({
                    'id': instance_id,
                    'socks_port': ports['socks_port'],
                    'ctrl_port': ports['ctrl_port'],
                    'http_port': ports['http_port']
                })

            # Update HAProxy configuration with subnet instances
            self._update_haproxy_config()

            # Add to active subnets
            self.active_subnets.add(subnet_key)

            return True

        except Exception as e:
            logger.error(f"Error starting Tor for subnet {subnet}: {e}")
            return False

    def stop_subnet_tor(self, subnet):
        """Stop Tor instances for a specific subnet"""
        try:
            subnet_key = f"{subnet}.0.0/16"

            # Stop Tor processes
            if subnet_key in self.subnet_tor_processes:
                for instance_id, process in self.subnet_tor_processes[subnet_key].items():
                    if process and process.poll() is None:
                        process.terminate()
                        logger.info(f"Stopped Tor instance {instance_id}")
                del self.subnet_tor_processes[subnet_key]

            # Stop Privoxy processes
            if subnet_key in self.subnet_privoxy_processes:
                for instance_id, process in self.subnet_privoxy_processes[subnet_key].items():
                    if process and process.poll() is None:
                        process.terminate()
                        logger.info(f"Stopped Privoxy instance {instance_id}")
                del self.subnet_privoxy_processes[subnet_key]

            # Remove from active subnets
            self.active_subnets.discard(subnet_key)

            # Update HAProxy configuration
            self._update_haproxy_config()

            return True

        except Exception as e:
            logger.error(f"Error stopping Tor for subnet {subnet}: {e}")
            return False

    def _update_haproxy_config(self):
        """Update HAProxy configuration with current instances"""
        try:
            # Collect all running instances
            all_instances = []
            
            # Add main instances
            for i in range(1, self.stats.get('tor_instances', 0) + 1):
                if i in self.tor_processes and self.tor_processes[i].poll() is None:
                    ports = self.config_manager.get_port_assignment(i)
                    all_instances.append({
                        'id': i,
                        'socks_port': ports['socks_port'],
                        'ctrl_port': ports['ctrl_port'],
                        'http_port': ports['http_port']
                    })

            # Add subnet instances
            for subnet_key, processes in self.subnet_tor_processes.items():
                if processes:  # Only include subnets with running processes
                    for instance_id in processes.keys():
                        # Extract numeric ID from string instance_id (e.g., "185.220_1" -> 1)
                        numeric_id = int(instance_id.split('_')[-1])
                        ports = self.config_manager.get_port_assignment(numeric_id)
                        all_instances.append({
                            'id': instance_id,
                            'socks_port': ports['socks_port'],
                            'ctrl_port': ports['ctrl_port'],
                            'http_port': ports['http_port']
                        })
              # Update HAProxy config
            self.config_manager.haproxy_manager.create_config(all_instances)
            
            # Use Runtime API for dynamic update (fallback to traditional reload if needed)
            success = self.config_manager.haproxy_manager.reload_runtime_api(all_instances)
            if success:
                logger.info(f"HAProxy configuration updated with {len(all_instances)} instances via Runtime API")
            else:
                logger.error("Failed to update HAProxy configuration")
                
            return success
            
        except Exception as e:
            logger.error(f"Error updating HAProxy configuration: {e}")
            return False

    def restart_subnet_tor(self, subnet, instances_count=1):
        """Restart Tor instances for a specific subnet"""
        try:
            self.stop_subnet_tor(subnet)
            time.sleep(2)  # Wait for processes to terminate
            return self.start_subnet_tor(subnet, instances_count)
        except Exception as e:
            logger.error(f"Error restarting Tor for subnet {subnet}: {e}")
            return False

    def _reload_haproxy(self):
        """Legacy method - now uses Runtime API for dynamic updates"""
        # Get current instances for Runtime API update
        all_instances = []
        for subnet, processes in self.subnet_tor_processes.items():
            for instance_id in processes.keys():
                ports = self.config_manager.get_port_assignment(instance_id)
                all_instances.append({
                    'instance_id': instance_id,
                    'subnet': subnet,
                    'http_port': ports['http_port']
                })
        
        return self.config_manager.haproxy_manager.reload_runtime_api(all_instances)

# Global manager instance
tor_manager = TorNetworkManager()


@app.route('/')
def index():
    """Main admin panel page"""
    # Start monitoring when first user connects
    tor_manager.start_monitoring()
    # Initialize subnet stats
    tor_manager.update_subnet_stats()
    return render_template('admin.html')


@app.route('/api/status')
def api_status():
    """Get current status of all services"""
    service_status = tor_manager.get_service_status()

    return jsonify({
        'services': service_status,
        'stats': tor_manager.stats
    })


@app.route('/api/start', methods=['POST'])
def api_start():
    """Start Tor and Privoxy services"""
    try:
        success = tor_manager.start_services()
        if success:
            return jsonify({'success': True, 'message': 'Services started successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to start services'})
    except Exception as e:
        logger.error(f"Error in start API: {e}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/stop', methods=['POST'])
def api_stop():
    """Stop Tor and Privoxy services"""
    try:
        success = tor_manager.stop_services()
        if success:
            return jsonify({'success': True, 'message': 'Services stopped successfully'})
        else:
            return jsonify({'success': False, 'message': 'Failed to stop services'})
    except Exception as e:
        logger.error(f"Error in stop API: {e}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/api/services/start', methods=['POST'])
def api_services_start():
    """Start all services"""
    try:
        success = tor_manager.start_services()
        status = tor_manager.get_service_status()
        return jsonify({'success': success, 'status': status})
    except Exception as e:
        logger.error(f"Error starting services: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/services/stop', methods=['POST'])
def api_services_stop():
    """Stop all services"""
    try:
        success = tor_manager.stop_services()
        status = tor_manager.get_service_status()
        return jsonify({'success': success, 'status': status})
    except Exception as e:
        logger.error(f"Error stopping services: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/services/restart', methods=['POST'])
def api_services_restart():
    """Restart all services"""
    try:
        tor_manager.stop_services()
        time.sleep(2)  # Wait a bit for processes to terminate
        success = tor_manager.start_services()
        status = tor_manager.get_service_status()
        return jsonify({'success': success, 'status': status})
    except Exception as e:
        logger.error(f"Error restarting services: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/services/status')
def api_services_status():
    """Get current service status"""
    try:
        status = tor_manager.get_service_status()
        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting service status: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/subnets')
def api_subnets():
    """Get subnet data"""
    try:
        # Trigger an update of subnet stats
        tor_manager.update_subnet_stats()

        # Collect subnet data from the tor_manager
        relay_data = tor_manager.fetch_tor_relays()
        if not relay_data or 'relays' not in relay_data:
            return jsonify({'success': False, 'error': 'Failed to fetch relay data'})

        # Process relays by subnet (similar to emit_subnet_data function)
        subnet_data = {}
        for relay in relay_data['relays']:
            if 'or_addresses' in relay:
                for addr in relay['or_addresses']:
                    ip = addr.split(':')[0]
                    if '.' in ip:  # IPv4
                        subnet_prefix = '.'.join(ip.split('.')[:2])
                        subnet = f"{subnet_prefix}.0.0/16"

                        if subnet not in subnet_data:
                            # Check if this subnet is active
                            is_subnet_active = subnet in tor_manager.active_subnets
                            subnet_data[subnet] = {
                                'subnet': subnet_prefix,
                                'total_relays': 0,
                                'active_relays': 0,
                                'countries': set(),
                                'status': 'running' if is_subnet_active else 'stopped',
                                'is_active': is_subnet_active,
                                'limit': 0,
                                'running_instances': 1 if is_subnet_active else 0,
                                'instances_count': 1
                            }

                        subnet_data[subnet]['total_relays'] += 1
                        subnet_data[subnet]['active_relays'] += 1

                        if 'country' in relay and relay['country']:
                            subnet_data[subnet]['countries'].add(
                                relay['country'])

        # Convert to list and prepare for JSON serialization
        subnet_list = []
        for subnet, data in subnet_data.items():
            subnet_entry = data.copy()
            subnet_entry['countries'] = list(data['countries'])
            subnet_list.append(subnet_entry)

        # Sort by number of relays
        subnet_list.sort(key=lambda x: x['total_relays'], reverse=True)

        # Limit to top 300 subnets for better performance
        subnet_list = subnet_list[:300]

        return jsonify({
            'success': True,
            'subnets': subnet_list,
            'stats': tor_manager.stats
        })
    except Exception as e:
        logger.error(f"Error getting subnet data: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/subnet/<subnet>/toggle', methods=['POST'])
def api_toggle_subnet(subnet):
    """Toggle subnet active status"""
    try:
        logger.info(f"Toggle request for subnet: {subnet}")
        # Find subnet in active_subnets
        subnet_key = f"{subnet}.0.0/16"
        is_active = subnet_key in tor_manager.active_subnets

        logger.info(
            f"Subnet {subnet} current status: {'active' if is_active else 'inactive'}")
        logger.info(f"Active subnets: {list(tor_manager.active_subnets)}")

        # Toggle status
        if is_active:
            logger.info(f"Stopping subnet {subnet}...")
            # Stop the subnet
            success = tor_manager.stop_subnet_tor(subnet)
            if not success:
                logger.error(f"Failed to stop subnet {subnet}")
                return jsonify({'success': False, 'error': 'Failed to stop subnet'})
        else:
            logger.info(f"Starting subnet {subnet}...")
            # Start the subnet
            success = tor_manager.start_subnet_tor(subnet, instances_count=1)
            if not success:
                logger.error(f"Failed to start subnet {subnet}")
                return jsonify({'success': False, 'error': 'Failed to start subnet'})

        # Get new status
        is_active = subnet_key in tor_manager.active_subnets
        logger.info(
            f"Subnet {subnet} new status: {'active' if is_active else 'inactive'}")

        # Update statistics
        tor_manager.stats['active_subnets'] = len(tor_manager.active_subnets)

        # Emit subnet status update
        socketio.emit('subnet_status_update', {
            'subnet': subnet,
            'is_active': is_active,
            'status': 'running' if is_active else 'stopped'
        })

        return jsonify({
            'success': True,
            'subnet': subnet,
            'active': is_active,
            'message': f"Subnet {subnet} {'started' if is_active else 'stopped'} successfully"
        })
    except Exception as e:
        logger.error(f"Error toggling subnet {subnet}: {e}", exc_info=True)
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/subnet/<subnet>/limit', methods=['POST'])
def api_set_subnet_limit(subnet):
    """Set instance limit for a subnet"""
    try:
        data = request.get_json()
        limit = data.get('limit', 1)

        subnet_key = f"{subnet}.0.0/16"
        tor_manager.subnet_limits[subnet_key] = limit

        return jsonify({
            'success': True,
            'subnet': subnet,
            'limit': limit,
            'message': f'Limit for subnet {subnet} set to {limit}'
        })
    except Exception as e:
        logger.error(f"Error setting limit for subnet {subnet}: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/haproxy/status')
def api_haproxy_status():
    """Get HAProxy status"""
    try:
        status = {
            'running': tor_manager.config_manager.haproxy_manager.is_running(),
            'config_valid': True,  # We'll assume it's valid if we got this far
            'config_error': None  # Add this as default
        }
          # Validate config
        try:
            config_path = tor_manager.config_manager.haproxy_manager.config_path
            validate_cmd = ['haproxy', '-c', '-f', config_path]
            result = subprocess.run(validate_cmd, capture_output=True, text=True)
            status['config_valid'] = result.returncode == 0
            if not status['config_valid']:
                status['config_error'] = result.stderr
        except Exception as e:
            status['config_valid'] = False
            status['config_error'] = str(e)
        
        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting HAProxy status: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/haproxy/reload', methods=['POST'])
def api_haproxy_reload():
    """Reload HAProxy configuration using Runtime API"""
    try:
        # Get current instances for Runtime API update
        all_instances = []
        for subnet, processes in tor_manager.subnet_tor_processes.items():
            for instance_id in processes.keys():
                ports = tor_manager.config_manager.get_port_assignment(instance_id)
                all_instances.append({
                    'instance_id': instance_id,
                    'subnet': subnet,
                    'http_port': ports['http_port']
                })
        
        success = tor_manager.config_manager.haproxy_manager.reload_runtime_api(all_instances)
        if success:
            return jsonify({'success': True, 'message': 'HAProxy reloaded successfully via Runtime API'})
        else:
            return jsonify({'success': False, 'message': 'Failed to reload HAProxy'})
    except Exception as e:
        logger.error(f"Error reloading HAProxy: {e}")
        return jsonify({'success': False, 'error': str(e)})



@app.route('/api/haproxy/stats', methods=['GET'])
def api_haproxy_stats():
    """Get HAProxy statistics via Runtime API"""
    try:
        stats = tor_manager.config_manager.haproxy_manager.get_stats()
        if stats:
            return jsonify({'success': True, 'stats': stats})
        else:
            return jsonify({'success': False, 'message': 'Failed to get HAProxy stats'})
    except Exception as e:
        logger.error(f"Error getting HAProxy stats: {e}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/haproxy/backend/servers', methods=['GET'])
def api_haproxy_backend_servers():
    """Get backend servers via Runtime API"""
    try:
        backend = request.args.get('backend', 'privoxy')
        servers = tor_manager.config_manager.get_backend_servers(backend)
        return jsonify({'success': True, 'backend': backend, 'servers': servers})
    except Exception as e:
        logger.error(f"Error getting backend servers: {e}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    # Start the monitoring
    tor_manager.start_monitoring()

    logger.info("Starting Tor Network Admin Panel on http://0.0.0.0:5000")

    # Run the Flask app with SocketIO
    socketio.run(app,
                 host='0.0.0.0',
                 port=5000,
                 debug=False,  # Set to False for production
                 allow_unsafe_werkzeug=True)
