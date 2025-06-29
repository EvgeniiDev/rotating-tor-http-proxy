#!/usr/bin/env python3
import logging
from flask import render_template, request, jsonify

from models import create_success_response, create_error_response

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class AdminPanel:
    def __init__(self, app, socketio, http_balancer, tor_manager):
        self.app = app
        self.socketio = socketio
        self.http_balancer = http_balancer
        self.tor_manager = tor_manager
        
        self._register_routes()
    
    def _register_routes(self):
        self.app.route('/')(self.index)
        self.app.route('/health')(self.health_check)
        self.app.route('/api/subnets')(self.get_subnets)
        self.app.route('/api/status')(self.get_status)
        self.app.route('/api/subnet/<subnet>/start', methods=['POST'])(self.start_subnet)
        self.app.route('/api/subnet/<subnet>/stop', methods=['POST'])(self.stop_subnet)
        self.app.route('/api/subnet/<subnet>/limit', methods=['PUT'])(self.set_subnet_limit)
        self.app.route('/api/subnet/<subnet>/instances', methods=['PUT'])(self.set_subnet_instances)
    
    def index(self):
        return render_template('admin.html')
    
    def health_check(self):
        return jsonify({'status': 'ok'})
    
    def get_subnets(self):
        try:
            running_instances = self.tor_manager.get_running_instances()
            
            subnet_data = []
            for i, port in enumerate(running_instances):
                subnet_name = f"192.168.{i//10}.0"
                subnet_data.append({
                    'subnet': subnet_name,
                    'instances': 1,
                    'limit': 10,
                    'count': 50,
                    'status': 'active'
                })
            
            return jsonify({
                'success': True,
                'subnets': subnet_data
            })
        except Exception as e:
            logger.error(f"Error getting subnets: {e}")
            return jsonify({
                'success': False,
                'error': str(e),
                'subnets': []
            })

    def get_status(self):
        try:
            tor_status = self.tor_manager.get_service_status()
            return jsonify({
                'tor_network': tor_status
            })
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            return jsonify({
                'tor_network': {
                    'status': 'error',
                    'error': str(e)
                }
            })

    def start_subnet(self, subnet):
        data = request.get_json() or {}
        instances_count = data.get('instances', 1)
        
        return jsonify({
            'success': True,
            'message': f'Subnet {subnet} started with {instances_count} instances'
        })

    def stop_subnet(self, subnet):
        return jsonify({
            'success': True,
            'message': f'Subnet {subnet} stopped'
        })

    def set_subnet_limit(self, subnet):
        data = request.get_json()
        if not data or 'limit' not in data:
            return jsonify(create_error_response('Limit value is required')), 400

        limit = data['limit']
        return jsonify(create_success_response(f'Set limit for subnet {subnet} to {limit} instances'))

    def set_subnet_instances(self, subnet):
        data = request.get_json()
        if not data or 'instances' not in data:
            return jsonify(create_error_response('Instances value is required')), 400

        instances = data['instances']
        return jsonify(create_success_response(f'Set instances for subnet {subnet} to {instances}'))
