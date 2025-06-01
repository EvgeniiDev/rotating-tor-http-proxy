#!/usr/bin/env python3
import time
import signal
import logging
from collections import defaultdict
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

from tor_network_manager import TorNetworkManager
from models import (
    SubnetData, Stats, get_current_timestamp, create_success_response, create_error_response
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tor-admin-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')


# Initialize the manager
tor_manager = TorNetworkManager(socketio)
tor_manager.start_monitoring()


# API endpoints
@app.route('/')
def index():
    return render_template('admin.html')

def _create_subnet_data(subnet_counts):
    """Helper function to create subnet data"""
    subnet_data_list = []
    for subnet, count in sorted(subnet_counts.items(), key=lambda x: x[1], reverse=True):
        status = 'active' if subnet in tor_manager.active_subnets else 'available'
        limit = tor_manager.subnet_limits.get(subnet, 1)
        
        subnet_key = f"subnet_{subnet.replace('.', '_')}"
        running_instances = len([
            p for p in tor_manager.subnet_tor_processes.get(subnet_key, {}).values()
            if p and p.poll() is None
        ])

        subnet_data = SubnetData(
            subnet=subnet,
            count=count,
            status=status,
            limit=limit,
            running_instances=running_instances,
            last_updated=get_current_timestamp()
        )
        subnet_data_list.append(subnet_data.to_dict())
    return subnet_data_list

@app.route('/api/subnets')
def get_subnets():
    if not tor_manager.current_relays:
        relay_data = tor_manager.fetch_tor_relays()
        if relay_data:
            tor_manager.current_relays = tor_manager.extract_relay_ips(relay_data)

    subnet_counts = defaultdict(int)
    for relay in tor_manager.current_relays or []:
        ip_parts = relay['ip'].split('.')
        if len(ip_parts) >= 2:
            subnet = f"{ip_parts[0]}.{ip_parts[1]}"
            subnet_counts[subnet] += 1

    subnet_data_list = _create_subnet_data(subnet_counts)

    # Update stats with new calculations
    tor_manager.update_subnet_stats()

    return jsonify({
        'success': True,
        'subnets': subnet_data_list,
        'stats': {
            'active_subnets': tor_manager.stats.get('active_subnets', 0),
            'blocked_subnets': tor_manager.stats.get('blocked_subnets', 0),
            'occupied_subnets': tor_manager.stats.get('occupied_subnets', 0),
            'free_subnets': tor_manager.stats.get('free_subnets', 0),
            'total_subnets': tor_manager.stats.get('total_subnets', len(subnet_counts)),
            'tor_instances': len(tor_manager.tor_processes),
            'running_instances': tor_manager.stats.get('running_instances', 0),
            'last_update': tor_manager.stats.get('last_update')
        }
    })

@app.route('/api/status')
def get_status():
    return jsonify(tor_manager.get_service_status())

def _handle_service_operation(operation_name, operation_func, *args):
    """Helper function to handle service operations"""
    try:
        success = operation_func(*args)
        if success:
            return jsonify(create_success_response(f'{operation_name} successful'))
        else:
            return jsonify(create_error_response(f'{operation_name} failed'))
    except Exception as e:
        logger.error(f"Error in {operation_name}: {e}")
        return jsonify(create_error_response(operation_name, str(e))), 500
        
@app.route('/api/services/start', methods=['POST'])
def start_services():
    return _handle_service_operation("Service start", tor_manager.start_services)

@app.route('/api/services/stop', methods=['POST'])
def stop_services():
    return _handle_service_operation("Service stop", tor_manager.stop_services)

@app.route('/api/services/restart', methods=['POST'])
def restart_services():
    def restart():
        tor_manager.stop_services()
        time.sleep(2)
        return tor_manager.start_services()
    return _handle_service_operation("Service restart", restart)

@app.route('/api/subnet/<subnet>/restart', methods=['POST'])
def restart_subnet(subnet):
    data = request.get_json() or {}
    instances_count = data.get('instances', 1)
    return _handle_service_operation(
        f"Restart subnet {subnet}",
        tor_manager.restart_subnet_tor,
        subnet, instances_count
    )

@app.route('/api/subnet/<subnet>/start', methods=['POST'])
def start_subnet(subnet):
    data = request.get_json() or {}
    instances_count = data.get('instances', 1)
    return _handle_service_operation(
        f"Start subnet {subnet}",
        tor_manager.start_subnet_tor,
        subnet, instances_count
    )

@app.route('/api/subnet/<subnet>/stop', methods=['POST'])
def stop_subnet(subnet):
    return _handle_service_operation(
        f"Stop subnet {subnet}",
        tor_manager.stop_subnet_tor,
        subnet
    )


@app.route('/api/subnet/<subnet>/limit', methods=['PUT'])
def set_subnet_limit(subnet):
    data = request.get_json()
    if not data or 'limit' not in data:
        return jsonify(create_error_response('Limit value is required')), 400

    limit = data['limit']
    if not isinstance(limit, int) or limit < 1:
        return jsonify(create_error_response('Limit must be a positive integer')), 400

    tor_manager.subnet_limits[subnet] = limit
    return jsonify(create_success_response(f'Set limit for subnet {subnet} to {limit} instances'))

@app.route('/api/subnet/<subnet>/instances', methods=['PUT'])
def set_subnet_instances(subnet):
    data = request.get_json()
    if not data or 'instances' not in data:
        return jsonify(create_error_response('Instances value is required')), 400

    instances = data['instances']
    if not isinstance(instances, int) or instances < 0:
        return jsonify(create_error_response('Instances must be a non-negative integer')), 400

    limit = tor_manager.subnet_limits.get(subnet, 10)
    if instances > limit:
        return jsonify(create_error_response(f'Instances count ({instances}) exceeds limit ({limit})')), 400

    if instances == 0:
        success = tor_manager.stop_subnet_tor(subnet)
        message = f'Stopped all instances for subnet {subnet}'
    else:
        tor_manager.stop_subnet_tor(subnet)
        success = tor_manager.start_subnet_tor(subnet, instances)
        message = f'Set {instances} instances for subnet {subnet}'

    if success:
        return jsonify(create_success_response(message))
    else:
        return jsonify(create_error_response(f'Failed to set instances for subnet {subnet}'))


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
