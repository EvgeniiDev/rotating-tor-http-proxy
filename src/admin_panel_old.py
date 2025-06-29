#!/usr/bin/env python3
import time
import signal
import logging
from collections import defaultdict
from flask import render_template, request, jsonify

from models import (
    SubnetData, get_current_timestamp, create_success_response, create_error_response
)

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)


class AdminPanel:
    def __init__(self, app, socketio, http_balancer, tor_manager):
        """Инициализация админ-панели с внешними зависимостями"""
        self.app = app
        self.socketio = socketio
        self.http_balancer = http_balancer
        self.tor_manager = tor_manager
        
        # Регистрируем маршруты
        self._register_routes()
    
    def _register_routes(self):
        """Регистрация всех маршрутов"""
        self.app.route('/')(self.index)
        self.app.route('/health')(self.health_check)
        self.app.route('/api/subnets')(self.get_subnets)
        self.app.route('/api/status')(self.get_status)
        self.app.route('/api/subnet/<subnet>/start', methods=['POST'])(self.start_subnet)
        self.app.route('/api/subnet/<subnet>/stop', methods=['POST'])(self.stop_subnet)
        self.app.route('/api/subnet/<subnet>/limit', methods=['PUT'])(self.set_subnet_limit)
        self.app.route('/api/subnet/<subnet>/instances', methods=['PUT'])(self.set_subnet_instances)
        self.app.route('/api/stats/comprehensive')(self.get_comprehensive_stats)
        self.app.route('/api/health/stats')(self.get_health_stats)
    
    def _handle_service_operation(self, operation_name, operation_func, *args):
        """Обработчик операций с сервисами"""
        try:
            success = operation_func(*args)
            if success:
                return jsonify(create_success_response(f'{operation_name} successful'))
            else:
                return jsonify(create_error_response(f'{operation_name} failed'))
        except Exception as e:
            logger.error(f"Error in {operation_name}: {e}")
            return jsonify(create_error_response(operation_name, str(e))), 500
    
    def _create_subnet_data(self, subnet_counts):
        """Создание данных о подсетях"""
        subnet_data_list = []
        for subnet, count in sorted(subnet_counts.items(), key=lambda x: x[1], reverse=True):
            status = 'active' if subnet in self.tor_manager.active_subnets else 'available'
            limit = self.tor_manager.subnet_limits.get(subnet, 1)
            
            # Use thread-safe method to get running instances
            running_instances = self.tor_manager.get_subnet_running_instances(subnet)

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

    def index(self):
        """Главная страница"""
        return render_template('admin.html')

    def health_check(self):
        """Проверка работоспособности сервиса"""
        try:
            # Простейшая проверка - просто вернуть 200 OK
            return jsonify(create_success_response('Service is up and running'))
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return jsonify(create_error_response('Health check failed', str(e))), 500

    def get_subnets(self):
        """Получение списка подсетей"""
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
        """Получение статуса сервисов"""
        tor_status = self.tor_manager.get_service_status()
        
        return jsonify({
            'tor_network': tor_status
        })

    def start_subnet(self, subnet):
        """Запуск подсети"""
        data = request.get_json() or {}
        instances_count = data.get('instances', 1)
        
        def start_and_update():
            result = self.tor_manager.start_subnet_tor(subnet, instances_count)
            return result
        
        return self._handle_service_operation(
            f"Start subnet {subnet}",
            start_and_update
        )

    def stop_subnet(self, subnet):
        """Остановка подсети"""
        def stop_and_update():
            result = self.tor_manager.stop_subnet_tor(subnet)
            return result
        
        return self._handle_service_operation(
            f"Stop subnet {subnet}",
            stop_and_update
        )

    def set_subnet_limit(self, subnet):
        """Установка лимита для подсети"""
        data = request.get_json()
        if not data or 'limit' not in data:
            return jsonify(create_error_response('Limit value is required')), 400

        limit = data['limit']
        if not isinstance(limit, int) or limit < 1:
            return jsonify(create_error_response('Limit must be a positive integer')), 400

        self.tor_manager.subnet_limits[subnet] = limit
        return jsonify(create_success_response(f'Set limit for subnet {subnet} to {limit} instances'))

    def set_subnet_instances(self, subnet):
        """Установка количества экземпляров для подсети"""
        data = request.get_json()
        if not data or 'instances' not in data:
            return jsonify(create_error_response('Instances value is required')), 400

        instances = data['instances']
        if not isinstance(instances, int) or instances < 0:
            return jsonify(create_error_response('Instances must be a non-negative integer')), 400

        limit = self.tor_manager.subnet_limits.get(subnet, 10)
        if instances > limit:
            return jsonify(create_error_response(f'Instances count ({instances}) exceeds limit ({limit})')), 400

        def set_instances_and_update():
            if instances == 0:
                result = self.tor_manager.stop_subnet_tor(subnet)
            else:
                # Используем restart_subnet_tor для безопасной перезагрузки
                result = self.tor_manager.restart_subnet_tor(subnet, instances)
        
            return result

        success = set_instances_and_update()
        if success:
            message = f'Stopped all instances for subnet {subnet}' if instances == 0 else f'Set {instances} instances for subnet {subnet}'
            return jsonify(create_success_response(message))
        else:
            return jsonify(create_error_response(f'Failed to set instances for subnet {subnet}'))

    def get_comprehensive_stats(self):
        """Получить полную статистику всех компонентов"""
        try:
            stats = self.tor_manager.get_comprehensive_stats()
            return jsonify(create_success_response('Stats retrieved successfully', stats))
        except Exception as e:
            logger.error(f"Error getting comprehensive stats: {e}")
            return jsonify(create_error_response(f'Failed to get stats: {str(e)}')), 500

    def get_health_stats(self):
        """Получить статистику здоровья Tor инстансов"""
        try:
            health_stats = self.tor_manager.get_health_stats()
            return jsonify(create_success_response('Health stats retrieved successfully', health_stats))
        except Exception as e:
            logger.error(f"Error getting health stats: {e}")
            return jsonify(create_error_response(f'Failed to get health stats: {str(e)}')), 500
