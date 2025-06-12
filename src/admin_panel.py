#!/usr/bin/env python3
import time
import signal
import logging
from collections import defaultdict
from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

from tor_network_manager import TorNetworkManager
from http_load_balancer import HTTPLoadBalancer
from models import (
    SubnetData, get_current_timestamp, create_success_response, create_error_response
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tor-admin-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Инициализируем компоненты
http_balancer = HTTPLoadBalancer(listen_port=8080)
tor_manager = TorNetworkManager(socketio, http_balancer)

# Запускаем мониторинг
tor_manager.start_monitoring()

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



@app.route('/')
def index():
    return render_template('admin.html')

def _create_subnet_data(subnet_counts):
    """Helper function to create subnet data"""
    subnet_data_list = []
    for subnet, count in sorted(subnet_counts.items(), key=lambda x: x[1], reverse=True):
        status = 'active' if subnet in tor_manager.active_subnets else 'available'
        limit = tor_manager.subnet_limits.get(subnet, 1)
        
        # Use thread-safe method to get running instances
        running_instances = tor_manager.get_subnet_running_instances(subnet)

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
    tor_status = tor_manager.get_service_status()
    balancer_stats = http_balancer.get_stats()
    
    return jsonify({
        'tor_network': tor_status,
        'http_balancer': {
            'running': http_balancer.server_thread and http_balancer.server_thread.is_alive(),
            'listen_port': http_balancer.listen_port,
            'stats': balancer_stats
        }
    })

@app.route('/api/balancer/stats')
def get_balancer_stats():
    """Получить подробную статистику HTTP балансировщика"""
    try:
        balancer_running = http_balancer.server_thread and http_balancer.server_thread.is_alive()
        
        # Получаем общую статистику балансировщика
        balancer_stats = http_balancer.get_stats()
        
        # Получаем упрощенную статистику всех прокси
        stats_data = http_balancer.stats_manager.get_all_stats()
        
        # Получаем summary статистику
        summary_stats = http_balancer.stats_manager.get_summary_stats()
        
        response_data = {
            'success': True,
            'stats': {
                'running': balancer_running,
                'listen_port': http_balancer.listen_port,
                'total_proxies': balancer_stats.get('total_proxies', 0),
                'available_proxies': balancer_stats.get('available_proxies', 0),
                'unavailable_proxies': balancer_stats.get('unavailable_proxies', 0),
                'available_proxy_ports': balancer_stats.get('available_proxy_ports', []),
                'unavailable_proxy_ports': balancer_stats.get('unavailable_proxy_ports', []),
                'current_index': balancer_stats.get('current_index', 0),
                
                # Упрощенная статистика прокси                'proxy_stats': stats_data,
                
                # Общая статистика
                'summary_stats': summary_stats
            }
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        logger.error(f"Error getting balancer stats: {e}")
        return jsonify(create_error_response(f"Failed to get balancer stats: {str(e)}"))



@app.route('/api/subnet/<subnet>/start', methods=['POST'])
def start_subnet(subnet):
    data = request.get_json() or {}
    instances_count = data.get('instances', 1)
    
    def start_and_update():
        result = tor_manager.start_subnet_tor(subnet, instances_count)
        return result
    
    return _handle_service_operation(
        f"Start subnet {subnet}",
        start_and_update
    )

@app.route('/api/subnet/<subnet>/stop', methods=['POST'])
def stop_subnet(subnet):
    def stop_and_update():
        result = tor_manager.stop_subnet_tor(subnet)
        return result
    
    return _handle_service_operation(
        f"Stop subnet {subnet}",
        stop_and_update
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

    def set_instances_and_update():
        if instances == 0:
            result = tor_manager.stop_subnet_tor(subnet)
        else:
            tor_manager.stop_subnet_tor(subnet)
            result = tor_manager.start_subnet_tor(subnet, instances)
    
        return result

    success = set_instances_and_update()
    if success:
        message = f'Stopped all instances for subnet {subnet}' if instances == 0 else f'Set {instances} instances for subnet {subnet}'
        return jsonify(create_success_response(message))
    else:
        return jsonify(create_error_response(f'Failed to set instances for subnet {subnet}'))


@app.route('/api/balancer/start', methods=['POST'])
def start_http_balancer():
    """Запуск HTTP балансировщика"""
    try:
        if http_balancer.server_thread and http_balancer.server_thread.is_alive():
            return jsonify(create_error_response('HTTP Load Balancer is already running'))
        
        http_balancer.start()
        return jsonify(create_success_response('HTTP Load Balancer started successfully'))
    except Exception as e:
        logger.error(f"Error starting HTTP Load Balancer: {e}")
        return jsonify(create_error_response(f'Failed to start HTTP Load Balancer: {str(e)}')), 500


@app.route('/api/balancer/stop', methods=['POST'])
def stop_http_balancer():
    """Остановка HTTP балансировщика"""
    try:
        if not (http_balancer.server_thread and http_balancer.server_thread.is_alive()):
            return jsonify(create_error_response('HTTP Load Balancer is not running'))
        
        http_balancer.stop()
        return jsonify(create_success_response('HTTP Load Balancer stopped successfully'))
    except Exception as e:
        logger.error(f"Error stopping HTTP Load Balancer: {e}")
        return jsonify(create_error_response(f'Failed to stop HTTP Load Balancer: {str(e)}')), 500


@app.route('/api/balancer/clear-stats', methods=['POST'])
def clear_balancer_stats():
    """Очистка статистики HTTP балансировщика"""
    try:
        if hasattr(http_balancer, 'stats_manager') and http_balancer.stats_manager:
            # Очищаем статистику в stats_manager
            http_balancer.stats_manager.clear_all_stats()
            return jsonify(create_success_response('Balancer statistics cleared successfully'))
        else:
            return jsonify(create_error_response('Statistics manager not available'))
    except Exception as e:
        logger.error(f"Error clearing balancer statistics: {e}")
        return jsonify(create_error_response(f'Failed to clear statistics: {str(e)}')), 500


@app.route('/api/services/start', methods=['POST'])
def start_all_services():
    """Запуск всех сервисов (Tor Network Manager + HTTP Load Balancer)"""
    try:
        # Запускаем инфраструктуру Tor Network Manager
        tor_success = tor_manager.start_services()
        
        # Запускаем HTTP балансировщик, если он не запущен
        balancer_success = True
        if not (http_balancer.server_thread and http_balancer.server_thread.is_alive()):
            http_balancer.start()
            balancer_success = http_balancer.server_thread and http_balancer.server_thread.is_alive()
        
        if tor_success and balancer_success:
            return jsonify(create_success_response('All services started successfully'))
        else:
            return jsonify(create_error_response('Failed to start some services'))
    except Exception as e:
        logger.error(f"Error starting services: {e}")
        return jsonify(create_error_response(f'Failed to start services: {str(e)}')), 500


@app.route('/api/services/stop', methods=['POST'])
def stop_all_services():
    """Остановка всех сервисов"""
    try:
        # Останавливаем HTTP балансировщик
        try:
            http_balancer.stop()
        except Exception as e:
            logger.warning(f"Error stopping HTTP Load Balancer: {e}")
        
        # Останавливаем Tor Network Manager
        tor_success = tor_manager.stop_services()
        
        return jsonify(create_success_response('All services stopped'))
    except Exception as e:
        logger.error(f"Error stopping services: {e}")
        return jsonify(create_error_response(f'Failed to stop services: {str(e)}')), 500


@app.route('/api/stats/comprehensive')
def get_comprehensive_stats():
    """Получить полную статистику всех компонентов"""
    try:
        stats = tor_manager.get_comprehensive_stats()
        return jsonify(create_success_response('Stats retrieved successfully', stats))
    except Exception as e:
        logger.error(f"Error getting comprehensive stats: {e}")
        return jsonify(create_error_response(f'Failed to get stats: {str(e)}')), 500


def signal_handler(signum, frame):
    logger.info("Received shutdown signal, stopping services...")
    try:
        http_balancer.stop()
    except:
        pass
    tor_manager.stop_monitoring()
    tor_manager.stop_services()
    exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

if __name__ == '__main__':
    logger.info("Starting Tor SOCKS5 Proxy Admin Panel with HTTP Load Balancer...")
    
    # Автоматически запускаем HTTP балансировщик при старте
    try:
        http_balancer.start()
        logger.info(f"HTTP Load Balancer started on port {http_balancer.listen_port}")
    except Exception as e:
        logger.error(f"Failed to start HTTP Load Balancer: {e}")
    
    # Запускаем инфраструктуру Tor Network Manager
    try:
        tor_manager.start_services()
        logger.info("Tor Network Manager infrastructure initialized")
    except Exception as e:
        logger.error(f"Failed to start Tor Network Manager: {e}")
    
    # Запускаем Flask приложение
    socketio.run(app, host='0.0.0.0', port=5000, debug=False, allow_unsafe_werkzeug=True)
