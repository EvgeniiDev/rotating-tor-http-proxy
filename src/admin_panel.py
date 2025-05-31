#!/usr/bin/env python3
import os
import json
import time
import signal
import logging
import threading
import subprocess
import requests
import re
from datetime import datetime
from collections import defaultdict
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit

# Import our config manager and models
from config_manager import ConfigManager
from models import (
    SubnetData, Stats, ServiceStatus, ApiResponse,
    SubnetRequest, ProxyTestResult, get_current_timestamp,
    create_success_response, create_error_response
)

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
        self.subnet_tor_processes = {}  # subnet -> {instance_id: process}
        self.config_manager = ConfigManager()  # Initialize config manager
        self.next_instance_id = 1  # Global instance ID counter - never resets, only increases
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
                
            self.services_started = True
            self.stats['tor_instances'] = 0  # No instances started yet
            self.update_running_instances_count()  # Properly count any existing instances

            logger.info("Services infrastructure initialized successfully. HAProxy is managed by shell script. Tor instances can now be started on demand.")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize services infrastructure: {e}")
            return False

    def stop_services(self):
        """Stop all Tor instances"""
        try:
            # Stop Tor processes
            for i, process in self.tor_processes.items():
                if process and process.poll() is None:
                    process.terminate()
                    logger.info(f"Stopped Tor instance {i}")

            # Stop subnet processes
            for subnet_key, processes in self.subnet_tor_processes.items():
                for instance_id, process in processes.items():
                    if process and process.poll() is None:
                        process.terminate()
                        logger.info(f"Stopped subnet Tor instance {instance_id}")

            self.tor_processes.clear()
            self.subnet_tor_processes.clear()
            self.active_subnets.clear()
            # HAProxy stop is handled by shell script
            self.services_started = False
            self.update_running_instances_count()  # Properly count remaining instances

            logger.info("All services stopped")

        except Exception as e:
            logger.error(f"Error stopping services: {e}")
            return False

        return True

    def get_service_status(self):
        """Check status of all running services"""
        running_tor = 0
        total_running = 0
        failed_instances = []

        # Check main Tor processes
        for instance_id, process in self.tor_processes.items():
            if process and process.poll() is None:
                running_tor += 1
            else:
                failed_instances.append(f"tor-{instance_id}")

        # Check subnet Tor processes
        subnet_running_tor = 0
        total_subnet_instances = 0

        for subnet_key, processes in self.subnet_tor_processes.items():
            for instance_id, process in processes.items():
                total_subnet_instances += 1
                if process and process.poll() is None:
                    subnet_running_tor += 1
                else:
                    failed_instances.append(f"subnet-tor-{instance_id}")

        total_running_tor = running_tor + subnet_running_tor
        total_running = total_running_tor
        
        # Check HAProxy status
        haproxy_running = self.config_manager.haproxy_manager.is_running()
        
        # Create ServiceStatus object
        status = ServiceStatus(
            services_started=self.services_started,
            total_instances=len(self.tor_processes) + total_subnet_instances,
            running_tor=total_running_tor,
            running_socks=total_running_tor,  # Same as running_tor since each Tor instance provides SOCKS
            haproxy_running=haproxy_running,
            failed_instances=failed_instances,
            last_check=get_current_timestamp()
        )
        
        return status.to_dict()

    def update_subnet_stats(self):
        """Update subnet statistics"""
        try:
            if not self.current_relays:
                relay_data = self.fetch_tor_relays()
                if relay_data:
                    self.current_relays = self.extract_relay_ips(relay_data)

            subnet_counts = defaultdict(int)
            for relay in self.current_relays:
                ip_parts = relay['ip'].split('.')
                if len(ip_parts) >= 2:
                    subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                    subnet_counts[subnet] += 1

            # Categorize subnets
            active_count = 0
            blocked_count = 0

            for subnet, count in subnet_counts.items():
                if subnet in self.active_subnets:
                    active_count += 1
                elif count < 5:  # Threshold for blocked
                    blocked_count += 1

            self.stats.update({
                'active_subnets': active_count,
                'blocked_subnets': blocked_count,
                'last_update': datetime.now().isoformat()
            })

        except Exception as e:
            logger.error(f"Error updating subnet stats: {e}")

    def emit_subnet_data(self, relays):
        """Emit subnet information to WebSocket clients"""
        try:
            subnet_counts = defaultdict(int)
            subnet_details = defaultdict(list)

            for relay in relays:
                ip_parts = relay['ip'].split('.')
                if len(ip_parts) >= 2:
                    subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                    subnet_counts[subnet] += 1
                    subnet_details[subnet].append({
                        'ip': relay['ip'],
                        'country': relay['country'],
                        'as_name': relay['as_name']
                    })

            # Sort by relay count
            sorted_subnets = sorted(subnet_counts.items(),
                                  key=lambda x: x[1], reverse=True)

            subnet_data = []
            for subnet, count in sorted_subnets[:100]:  # Top 100 subnets
                status = 'active' if subnet in self.active_subnets else 'available'
                limit = self.subnet_limits.get(subnet, 1)
                
                # Get instance count for this subnet
                subnet_key = f"subnet_{subnet.replace('.', '_')}"
                instance_count = 0
                if subnet_key in self.subnet_tor_processes:
                    instance_count = len(self.subnet_tor_processes[subnet_key])

                subnet_data.append({
                    'subnet': subnet,
                    'count': count,
                    'status': status,
                    'limit': limit,
                    'instance_count': instance_count,
                    'relays': subnet_details[subnet][:5]  # First 5 relays
                })

            socketio.emit('subnet_data', {
                'subnets': subnet_data,
                'stats': self.stats
            })

        except Exception as e:
            logger.error(f"Error emitting subnet data: {e}")

    def start_monitoring(self):
        """Start monitoring Tor relays and subnet information"""
        def monitor():
            while self.monitoring:
                try:
                    relay_data = self.fetch_tor_relays()
                    if relay_data:
                        relays = self.extract_relay_ips(relay_data)
                        self.current_relays = relays

                        # Update subnet stats
                        self.update_subnet_stats()

                        # Emit data to WebSocket clients
                        self.emit_subnet_data(relays)

                        logger.info(f"Fetched {len(relays)} Tor relay IPs")
                    else:
                        logger.warning("Failed to fetch relay data")

                    # Sleep for 5 minutes
                    for _ in range(300):
                        if not self.monitoring:
                            break
                        time.sleep(1)

                except Exception as e:
                    logger.error(f"Error in monitoring loop: {e}")
                    time.sleep(60)  # Wait 1 minute on error

        monitor_thread = threading.Thread(target=monitor)
        monitor_thread.daemon = True
        monitor_thread.start()
        logger.info("Started monitoring thread")

    def stop_monitoring(self):
        """Stop monitoring"""
        self.monitoring = False

    def start_subnet_tor(self, subnet, instances_count=1):
        """Start Tor instances for a specific subnet"""
        try:
            subnet_key = f"subnet_{subnet.replace('.', '_')}"

            if subnet_key not in self.subnet_tor_processes:
                self.subnet_tor_processes[subnet_key] = {}

            # Store the requested limit
            self.subnet_limits[subnet] = instances_count

            for i in range(instances_count):
                instance_id = self.get_next_available_id()

                # Create Tor config
                tor_config_result = self.config_manager.create_tor_config(
                    instance_id, subnet)

                # Start Tor process
                tor_cmd = [
                    'tor',
                    '-f', tor_config_result['config_path']
                ]

                logger.info(f"Starting Tor instance {instance_id} for subnet {subnet}")
                logger.info(f"Command: {' '.join(tor_cmd)}")

                process = subprocess.Popen(
                    tor_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                self.subnet_tor_processes[subnet_key][instance_id] = process

                # Add to HAProxy backend
                socks_port = tor_config_result['socks_port']
                self.config_manager.haproxy_manager.add_backend_instance(
                    instance_id, socks_port)

                logger.info(f"Started Tor instance {instance_id} for subnet {subnet} on SOCKS port {socks_port}")

            # Add subnet to active set
            self.active_subnets.add(subnet)

            # Update HAProxy configuration with all current instances
            self._update_haproxy_config()
            self.update_running_instances_count()

            logger.info(f"Started {instances_count} Tor instances for subnet {subnet}")
            return True

        except Exception as e:
            logger.error(f"Error starting Tor instances for subnet {subnet}: {e}")
            return False

    def stop_subnet_tor(self, subnet):
        """Stop Tor instances for a specific subnet"""
        try:
            subnet_key = f"subnet_{subnet.replace('.', '_')}"

            # Stop Tor processes
            if subnet_key in self.subnet_tor_processes:
                for instance_id, process in self.subnet_tor_processes[subnet_key].items():
                    if process and process.poll() is None:
                        process.terminate()
                        logger.info(f"Stopped Tor instance {instance_id} for subnet {subnet}")

                    # Remove from HAProxy backend
                    self.config_manager.haproxy_manager.remove_backend_instance(instance_id)

                del self.subnet_tor_processes[subnet_key]

            # Remove from active subnets
            self.active_subnets.discard(subnet)

            # Remove subnet limit
            if subnet in self.subnet_limits:
                del self.subnet_limits[subnet]

            # Update HAProxy configuration
            self._update_haproxy_config()
            self.update_running_instances_count()

            logger.info(f"Stopped all instances for subnet {subnet}")
            return True

        except Exception as e:
            logger.error(f"Error stopping subnet {subnet}: {e}")
            return False

    def _update_haproxy_config(self):
        """Update HAProxy configuration with current instances"""
        try:
            all_instances = []
            
            # Collect all running instances
            for subnet_key, processes in self.subnet_tor_processes.items():
                for instance_id, process in processes.items():
                    if process and process.poll() is None:
                        ports = self.config_manager.get_port_assignment(instance_id)
                        all_instances.append({
                            'id': instance_id,
                            'socks_port': ports['socks_port']
                        })

            # Update HAProxy
            success = self.config_manager.haproxy_manager.create_config(all_instances)
            if success:
                logger.info(f"Updated HAProxy with {len(all_instances)} instances")
            else:
                logger.error("Failed to update HAProxy configuration")

        except Exception as e:
            logger.error(f"Error updating HAProxy config: {e}")

    def restart_subnet_tor(self, subnet, instances_count=1):
        """Restart Tor instances for a subnet"""
        try:
            self.stop_subnet_tor(subnet)
            time.sleep(2)  # Brief pause
            return self.start_subnet_tor(subnet, instances_count)
        except Exception as e:
            logger.error(f"Error restarting subnet {subnet}: {e}")
            return False


    def update_running_instances_count(self):
        """Update the count of running instances"""
        try:
            running_count = 0
            
            # Count main instances
            for process in self.tor_processes.values():
                if process and process.poll() is None:
                    running_count += 1
            
            # Count subnet instances
            for processes in self.subnet_tor_processes.values():
                for process in processes.values():
                    if process and process.poll() is None:
                        running_count += 1

            self.stats['running_instances'] = running_count
            self.stats['tor_instances'] = running_count  # Total Tor instances

        except Exception as e:
            logger.error(f"Error updating running instances count: {e}")

    def get_next_available_id(self):
        """Get the next available instance ID"""
        current_id = self.next_instance_id
        self.next_instance_id += 1
        return current_id


# Initialize the manager
tor_manager = TorNetworkManager()

# Start monitoring when the application starts
tor_manager.start_monitoring()


@app.route('/')
def index():
    return render_template('admin.html')


@app.route('/api/subnets')
def get_subnets():
    try:
        if not tor_manager.current_relays:
            relay_data = tor_manager.fetch_tor_relays()
            if relay_data:
                tor_manager.current_relays = tor_manager.extract_relay_ips(relay_data)

        subnet_counts = defaultdict(int)
        for relay in tor_manager.current_relays:
            ip_parts = relay['ip'].split('.')
            if len(ip_parts) >= 2:
                subnet = f"{ip_parts[0]}.{ip_parts[1]}"
                subnet_counts[subnet] += 1

        sorted_subnets = sorted(subnet_counts.items(), key=lambda x: x[1], reverse=True)

        # Create subnet data using new model
        subnet_data_list = []
        for subnet, count in sorted_subnets[:100]:
            status = 'active' if subnet in tor_manager.active_subnets else 'available'
            limit = tor_manager.subnet_limits.get(subnet, 1)
            
            # Count running instances for this subnet
            running_instances = 0
            if subnet in tor_manager.subnet_tor_processes:
                for process in tor_manager.subnet_tor_processes[subnet].values():
                    if process and process.poll() is None:
                        running_instances += 1

            subnet_data = SubnetData(
                subnet=subnet,
                count=count,
                status=status,
                limit=limit,
                running_instances=running_instances,
                last_updated=get_current_timestamp()
            )
            subnet_data_list.append(subnet_data.to_dict())

        # Create stats using new model
        stats = Stats(
            active_subnets=len(tor_manager.active_subnets),
            blocked_subnets=0,  # TODO: implement blocked subnets tracking
            total_subnets=len(subnet_counts),
            tor_instances=len(tor_manager.tor_processes),
            running_instances=tor_manager.stats.get('running_instances', 0),
            last_update=get_current_timestamp()
        )

        return jsonify({
            'success': True,
            'subnets': subnet_data_list,
            'stats': stats.to_dict()
        })

    except Exception as e:
        logger.error(f"Error getting subnets: {e}")
        return jsonify(create_error_response("Failed to fetch subnets", str(e))), 500





@app.route('/api/status')
def get_status():
    try:
        status = tor_manager.get_service_status()
        return jsonify(status)
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        return jsonify({'error': str(e)}), 500





# Service endpoints for frontend compatibility
@app.route('/api/services/start', methods=['POST'])
def start_services_alt():
    """Alternative endpoint for starting services"""
    try:
        success = tor_manager.start_services()
        if success:
            return jsonify(create_success_response('Services initialized successfully'))
        else:
            return jsonify(create_error_response('Failed to initialize services'))
    except Exception as e:
        logger.error(f"Error starting services: {e}")
        return jsonify(create_error_response("Error starting services", str(e))), 500

@app.route('/api/services/stop', methods=['POST'])
def stop_services_alt():
    """Alternative endpoint for stopping services"""
    try:
        success = tor_manager.stop_services()
        message = 'Services stopped' if success else 'Failed to stop services'
        if success:
            return jsonify(create_success_response(message))
        else:
            return jsonify(create_error_response(message))
    except Exception as e:
        logger.error(f"Error stopping services: {e}")
        return jsonify(create_error_response("Error stopping services", str(e))), 500

@app.route('/api/services/restart', methods=['POST'])
def restart_services():
    """Restart all services"""
    try:
        tor_manager.stop_services()
        time.sleep(2)  # Give services time to stop
        success = tor_manager.start_services()
        if success:
            return jsonify(create_success_response('Services restarted successfully'))
        else:
            return jsonify(create_error_response('Failed to restart services'))
    except Exception as e:
        logger.error(f"Error restarting services: {e}")
        return jsonify(create_error_response("Error restarting services", str(e))), 500

@app.route('/api/test-proxy')
def test_proxy():
    """Test proxy functionality"""
    try:
        # Test proxy by making a request through it
        proxy_url = "http://127.0.0.1:8888"  # HAProxy load balancer
        test_url = "http://httpbin.org/ip"
        
        start_time = time.time()
        
        try:
            proxies = {
                'http': proxy_url,
                'https': proxy_url
            }
            response = requests.get(test_url, proxies=proxies, timeout=10)
            response_time = time.time() - start_time
            
            if response.status_code == 200:
                data = response.json()
                exit_ip = data.get('origin', 'Unknown')
                
                result = ProxyTestResult(
                    success=True,
                    ip=exit_ip,
                    response_time=response_time,
                    timestamp=get_current_timestamp()
                )
                return jsonify(result.to_dict())
            else:
                result = ProxyTestResult(
                    success=False,
                    error=f"HTTP {response.status_code}",
                    timestamp=get_current_timestamp()
                )
                return jsonify(result.to_dict())
                
        except requests.RequestException as req_err:
            result = ProxyTestResult(
                success=False,
                error=str(req_err),
                timestamp=get_current_timestamp()
            )
            return jsonify(result.to_dict())
            
    except Exception as e:
        logger.error(f"Error testing proxy: {e}")
        result = ProxyTestResult(
            success=False,
            error=str(e),
            timestamp=get_current_timestamp()
        )
        return jsonify(result.to_dict())

# Alternative subnet endpoints for frontend compatibility
@app.route('/api/subnet/<subnet>/restart', methods=['POST'])
def restart_subnet_alt(subnet):
    """Alternative endpoint for restarting subnet"""
    try:
        data = request.get_json() or {}
        instances_count = data.get('instances', 1)
        
        success = tor_manager.restart_subnet_tor(subnet, instances_count)
        if success:
            return jsonify(create_success_response(f'Restarted {instances_count} instances for subnet {subnet}'))
        else:
            return jsonify(create_error_response(f'Failed to restart instances for subnet {subnet}'))
            
    except Exception as e:
        logger.error(f"Error restarting subnet: {e}")
        return jsonify(create_error_response("Error restarting subnet", str(e))), 500

@app.route('/api/subnet/<subnet>/start', methods=['POST'])
def start_subnet_alt(subnet):
    """Alternative endpoint for starting subnet"""
    try:
        data = request.get_json() or {}
        instances_count = data.get('instances', 1)
        
        tor_manager.stop_services()
        success = tor_manager.start_subnet_tor(subnet, instances_count)
        if success:
            return jsonify(create_success_response(f'Started {instances_count} instances for subnet {subnet}'))
        else:
            return jsonify(create_error_response(f'Failed to start instances for subnet {subnet}'))
            
    except Exception as e:
        logger.error(f"Error starting subnet: {e}")
        return jsonify(create_error_response("Error starting subnet", str(e))), 500

@app.route('/api/subnet/<subnet>/stop', methods=['POST'])
def stop_subnet_alt(subnet):
    """Alternative endpoint for stopping subnet"""
    try:
        success = tor_manager.stop_subnet_tor(subnet)
        if success:
            return jsonify(create_success_response(f'Stopped instances for subnet {subnet}'))
        else:
            return jsonify(create_error_response(f'Failed to stop instances for subnet {subnet}'))
            
    except Exception as e:
        logger.error(f"Error stopping subnet: {e}")
        return jsonify(create_error_response("Error stopping subnet", str(e))), 500

@app.route('/api/subnet/<subnet>/limit', methods=['PUT'])
def set_subnet_limit(subnet):
    """Set the maximum number of addresses/instances for a subnet"""
    try:
        data = request.get_json()
        if not data or 'limit' not in data:
            return jsonify(create_error_response('Limit value is required')), 400
        
        limit = data['limit']
        if not isinstance(limit, int) or limit < 1:
            return jsonify(create_error_response('Limit must be a positive integer')), 400
        
        # Store the limit for this subnet
        tor_manager.subnet_limits[subnet] = limit
        
        return jsonify(create_success_response(f'Set limit for subnet {subnet} to {limit} instances'))
        
    except Exception as e:
        logger.error(f"Error setting subnet limit: {e}")
        return jsonify(create_error_response("Error setting subnet limit", str(e))), 500

@app.route('/api/subnet/<subnet>/instances', methods=['PUT'])
def set_subnet_instances(subnet):
    """Set the number of running instances for a subnet"""
    try:
        data = request.get_json()
        if not data or 'instances' not in data:
            return jsonify(create_error_response('Instances value is required')), 400
        
        instances = data['instances']
        if not isinstance(instances, int) or instances < 0:
            return jsonify(create_error_response('Instances must be a non-negative integer')), 400
        
        # Check if we have a limit set for this subnet
        limit = tor_manager.subnet_limits.get(subnet, 10)  # Default limit of 10
        if instances > limit:
            return jsonify(create_error_response(f'Instances count ({instances}) exceeds limit ({limit})')), 400
        
        if instances == 0:
            # Stop all instances for this subnet
            success = tor_manager.stop_subnet_tor(subnet)
            message = f'Stopped all instances for subnet {subnet}'
        else:
            # Start or adjust instances for this subnet
            tor_manager.stop_subnet_tor(subnet)  # Stop existing first
            success = tor_manager.start_subnet_tor(subnet, instances)
            message = f'Set {instances} instances for subnet {subnet}'
        
        if success:
            return jsonify(create_success_response(message))
        else:
            return jsonify(create_error_response(f'Failed to set instances for subnet {subnet}'))
        
    except Exception as e:
        logger.error(f"Error setting subnet instances: {e}")
        return jsonify(create_error_response("Error setting subnet instances", str(e))), 500


def signal_handler(signum, frame):
    logger.info("Received shutdown signal, stopping services...")
    tor_manager.stop_monitoring()
    tor_manager.stop_services()
    exit(0)


signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    logger.info("Starting Tor SOCKS5 Proxy Admin Panel...")
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
