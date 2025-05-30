#!/usr/bin/env python3
"""
Tor Network Admin Panel
Real-time management of Tor exit nodes and subnets
"""

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
        self.stats = {
            'total_relays': 0,
            'active_subnets': 0,
            'blocked_subnets': 0,
            'last_update': None
        }

    def fetch_tor_relays(self):
        """Fetch current Tor relay information"""
        try:
            url = "https://onionoo.torproject.org/details?type=relay&running=true&fields=or_addresses,country,as_name"
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(
                    f"Failed to fetch relay data: HTTP {response.status_code}")
                return None
        except Exception as e:
            logger.error(f"Error fetching relay data: {e}")
            return None

    def extract_subnet_info(self, relays_data):
        """Extract and group relay information by subnet"""
        subnet_groups = defaultdict(list)

        for relay in relays_data.get("relays", []):
            for address in relay.get("or_addresses", []):
                # Skip IPv6 addresses
                if "[" in address:
                    continue

                # Extract IP from IP:PORT format
                match = re.match(r"(\d+\.\d+\.\d+\.\d+):\d+", address)
                if match:
                    ip = match.group(1)
                    octets = ip.split(".")
                    subnet = f"{octets[0]}.{octets[1]}"

                    relay_info = {
                        'ip': ip,
                        'country': relay.get('country', 'Unknown'),
                        'as_name': relay.get('as_name', 'Unknown')
                    }
                    subnet_groups[subnet].append(relay_info)

        return subnet_groups

    def update_relay_data(self):
        """Update relay data and emit to clients"""
        while self.monitoring:
            try:
                relay_data = self.fetch_tor_relays()
                if relay_data:
                    subnet_groups = self.extract_subnet_info(relay_data)

                    # Filter significant subnets (with 5+ addresses)
                    significant_subnets = {
                        subnet: relays for subnet, relays in subnet_groups.items()
                        if len(relays) >= 5
                    }

                    self.current_relays = significant_subnets
                    self.stats.update({
                        'total_relays': sum(len(relays) for relays in significant_subnets.values()),
                        'active_subnets': len([s for s in significant_subnets.keys() if s in self.active_subnets]),
                        'blocked_subnets': len([s for s in significant_subnets.keys() if s not in self.active_subnets]),
                        'last_update': datetime.now().isoformat()
                    })                    # Emit update to all clients
                    socketio.emit('subnet_update', {
                        'subnets': self.prepare_subnet_data(),
                        'stats': self.stats
                    })

                time.sleep(30)  # Update every 30 seconds
            except Exception as e:
                logger.error(f"Error in update loop: {e}")
                time.sleep(60)

    def prepare_subnet_data(self):
        """Prepare subnet data for frontend"""
        subnet_data = []
        for subnet, relays in sorted(self.current_relays.items(),
                                     key=lambda x: len(x[1]), reverse=True):
            countries = list(
                set(relay['country'] for relay in relays if relay['country'] != 'Unknown'))

            subnet_info = {
                'subnet': subnet,
                'total_relays': len(relays),
                'active_relays': min(len(relays), self.subnet_limits.get(subnet, len(relays))),
                'countries': countries[:5],  # Top 5 countries
                'is_active': subnet in self.active_subnets,
                'limit': self.subnet_limits.get(subnet, len(relays))
            }
            subnet_data.append(subnet_info)

        return subnet_data

    def toggle_subnet(self, subnet):
        """Toggle subnet active state"""
        if subnet in self.active_subnets:
            self.active_subnets.remove(subnet)
            logger.info(f"Disabled subnet: {subnet}")
        else:
            self.active_subnets.add(subnet)
            logger.info(f"Enabled subnet: {subnet}")

        self.update_tor_config()
        return subnet in self.active_subnets

    def set_subnet_limit(self, subnet, limit):
        """Set address limit for subnet"""
        if limit <= 0:
            self.subnet_limits.pop(subnet, None)
        else:
            self.subnet_limits[subnet] = limit
            logger.info(f"Set subnet {subnet} limit to {limit}")
        self.update_tor_config()

    def update_tor_config(self):
        """Update Tor configuration and restart instances"""
        try:
            # Generate ExitNodes configuration
            exit_nodes = []
            for subnet in self.active_subnets:
                if subnet in self.current_relays:
                    relays = self.current_relays[subnet]
                    limit = self.subnet_limits.get(subnet, len(relays))
                    selected_relays = relays[:limit]
                    exit_nodes.extend([relay['ip'] for relay in selected_relays])

            # Update base Tor configuration file
            base_config_path = '/etc/tor/torrc.default'
            if os.path.exists(base_config_path):
                with open(base_config_path, 'r') as f:
                    base_lines = f.readlines()

                # Remove existing ExitNodes lines from base config
                base_lines = [
                    line for line in base_lines if not line.startswith('ExitNodes')]

                # Add new ExitNodes configuration to base config
                if exit_nodes:
                    # Limit to 50 nodes
                    base_lines.append(
                        f"ExitNodes {','.join(exit_nodes[:50])}\n")

                with open(base_config_path, 'w') as f:
                    f.writelines(base_lines)

                # Update all instance configurations
                for i in range(1, int(os.environ.get('TOR_INSTANCES', 10)) + 1):
                    instance_config = f'/etc/tor/torrc.{i}'
                    if os.path.exists(instance_config):
                        # Read instance-specific config
                        with open(instance_config, 'r') as f:
                            instance_lines = f.readlines()

                        # Remove existing ExitNodes lines
                        instance_lines = [
                            line for line in instance_lines if not line.startswith('ExitNodes')]

                        # Add new ExitNodes configuration
                        if exit_nodes:
                            instance_lines.append(
                                f"ExitNodes {','.join(exit_nodes[:50])}\n")

                        with open(instance_config, 'w') as f:
                            f.writelines(instance_lines)

                # Signal Tor instances to reload configuration
                self.reload_tor_config()

                logger.info(
                    f"Updated Tor config with {len(exit_nodes)} exit nodes from {len(self.active_subnets)} subnets")

        except Exception as e:
            logger.error(f"Error updating Tor config: {e}")

    def reload_tor_config(self):
        """Reload Tor configuration for all instances"""
        try:
            # Get all Tor process PIDs and send HUP signal
            tor_instances = int(os.environ.get('TOR_INSTANCES', 10))
            for i in range(1, tor_instances + 1):
                pid_file = f'/var/local/tor/{i}/tor.pid'
                if os.path.exists(pid_file):
                    try:
                        with open(pid_file, 'r') as f:
                            pid = int(f.read().strip())
                        os.kill(pid, 1)  # SIGHUP signal number
                        logger.info(
                            f"Sent reload signal to Tor instance {i} (PID: {pid})")
                    except (ValueError, ProcessLookupError, PermissionError) as e:
                        logger.warning(
                            f"Could not reload Tor instance {i}: {e}")

        except Exception as e:
            logger.error(f"Error reloading Tor config: {e}")


# Global manager instance
tor_manager = TorNetworkManager()


@app.route('/')
def index():
    """Main admin panel page"""
    return render_template('admin.html')


@app.route('/api/subnets')
def get_subnets():
    """Get current subnet information"""
    return jsonify({
        'subnets': tor_manager.prepare_subnet_data(),
        'stats': tor_manager.stats
    })


@app.route('/api/subnet/<subnet>/toggle', methods=['POST'])
def toggle_subnet(subnet):
    """Toggle subnet active state"""
    is_active = tor_manager.toggle_subnet(subnet)
    return jsonify({'success': True, 'active': is_active})


@app.route('/api/subnet/<subnet>/limit', methods=['POST'])
def set_subnet_limit(subnet):
    """Set subnet address limit"""
    data = request.get_json()
    limit = data.get('limit', 0)

    try:
        limit = int(limit)
        tor_manager.set_subnet_limit(subnet, limit)
        return jsonify({'success': True, 'limit': limit})
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid limit value'}), 400


@socketio.on('connect')
def handle_connect():
    """Handle client connection"""
    logger.info('Client connected')
    # Send current data to new client
    emit('subnet_update', {
        'subnets': tor_manager.prepare_subnet_data(),
        'stats': tor_manager.stats
    })


@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection"""
    logger.info('Client disconnected')


def signal_handler(sig, frame):
    """Handle shutdown signal"""
    logger.info('Shutting down admin panel...')
    tor_manager.monitoring = False
    os._exit(0)


if __name__ == '__main__':
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start background monitoring thread
    monitor_thread = threading.Thread(
        target=tor_manager.update_relay_data, daemon=True)
    monitor_thread.start()    # Start the web application
    logger.info('Starting Tor Network Admin Panel on port 5000...')
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
