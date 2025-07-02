import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

class BalancerDiagnostics:
    def __init__(self, pool_manager, load_balancer):
        self.pool_manager = pool_manager
        self.load_balancer = load_balancer
    
    def diagnose_missing_proxies(self) -> Dict:
        with self.pool_manager._lock:
            total_instances = len(self.pool_manager.instances)
            added_to_balancer = len(self.pool_manager.added_to_balancer)
            
            missing_count = total_instances - added_to_balancer
            
            running_instances = []
            healthy_instances = []
            unhealthy_instances = []
            not_in_balancer = []
            
            for port, instance in self.pool_manager.instances.items():
                status = {
                    'port': port,
                    'is_running': instance.is_running,
                    'is_healthy': None,
                    'in_balancer': port in self.pool_manager.added_to_balancer,
                    'failed_checks': instance.failed_checks,
                    'current_exit_ip': instance.current_exit_ip
                }
                
                if instance.is_running:
                    running_instances.append(status)
                    try:
                        is_healthy = instance.is_healthy()
                        status['is_healthy'] = is_healthy
                        if is_healthy:
                            healthy_instances.append(status)
                        else:
                            unhealthy_instances.append(status)
                    except Exception as e:
                        status['health_check_error'] = str(e)
                        unhealthy_instances.append(status)
                        
                    if port not in self.pool_manager.added_to_balancer:
                        not_in_balancer.append(status)
            
            balancer_stats = self.load_balancer.get_stats()
            
            return {
                'total_instances': total_instances,
                'running_instances': len(running_instances),
                'healthy_instances': len(healthy_instances),
                'unhealthy_instances': len(unhealthy_instances),
                'added_to_balancer': added_to_balancer,
                'missing_from_balancer': missing_count,
                'not_in_balancer': not_in_balancer,
                'balancer_stats': balancer_stats,
                'sample_unhealthy': unhealthy_instances[:5],
                'sample_not_in_balancer': not_in_balancer[:5]
            }
    
    def force_add_missing_proxies(self) -> int:
        added_count = 0
        with self.pool_manager._lock:
            logger.info("Starting force add missing proxies...")
            for port, instance in self.pool_manager.instances.items():
                if (instance.is_running and 
                    port not in self.pool_manager.added_to_balancer):
                    try:
                        is_healthy = instance.is_healthy()
                        logger.info(f"Port {port}: running=True, healthy={is_healthy}, failed_checks={instance.failed_checks}")
                        
                        if is_healthy or instance.failed_checks >= 5:
                            self.pool_manager._add_to_load_balancer(port)
                            added_count += 1
                            logger.info(f"Force added instance {port} to balancer (healthy={is_healthy})")
                        else:
                            logger.warning(f"Skipping port {port}: not healthy and only {instance.failed_checks} failed checks")
                    except Exception as e:
                        logger.error(f"Failed to force add {port}: {e}")
                        
            logger.info(f"Force add completed: {added_count} proxies added")
        return added_count
    
    def get_detailed_status(self) -> Dict:
        with self.pool_manager._lock:
            detailed_instances = []
            
            for port, instance in self.pool_manager.instances.items():
                try:
                    status = {
                        'port': port,
                        'is_running': instance.is_running,
                        'is_healthy': instance.is_healthy() if instance.is_running else False,
                        'in_balancer': port in self.pool_manager.added_to_balancer,
                        'failed_checks': instance.failed_checks,
                        'current_exit_ip': instance.current_exit_ip,
                        'process_alive': instance.process.poll() is None if instance.process else False
                    }
                except Exception as e:
                    status = {
                        'port': port,
                        'error': str(e),
                        'is_running': False,
                        'is_healthy': False,
                        'in_balancer': port in self.pool_manager.added_to_balancer,
                        'failed_checks': getattr(instance, 'failed_checks', 'unknown'),
                        'current_exit_ip': getattr(instance, 'current_exit_ip', 'unknown'),
                        'process_alive': False
                    }
                detailed_instances.append(status)
            
            return {
                'instances': detailed_instances,
                'summary': {
                    'total': len(detailed_instances),
                    'running': len([i for i in detailed_instances if i['is_running']]),
                    'healthy': len([i for i in detailed_instances if i.get('is_healthy', False)]),
                    'in_balancer': len([i for i in detailed_instances if i['in_balancer']]),
                    'missing_from_balancer': len([i for i in detailed_instances if i['is_running'] and not i['in_balancer']])
                }
            }
