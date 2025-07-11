import asyncio
import logging
import socket
import time
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from tor_process import TorProcess

logger = logging.getLogger(__name__)

class ParallelWorkerManager:
    def __init__(self, port_start: int = 30100, max_concurrent: int = 10):
        self.port_start = port_start
        self.max_concurrent = max_concurrent
    
    def find_free_ports(self, count: int, extra_ports: int = 30) -> List[int]:
        ports = []
        for i in range(count + extra_ports):
            port = self._find_free_port(self.port_start + i * 10)
            if port:
                ports.append(port)
            if len(ports) >= count * 2:
                break
        return ports
    
    def _find_free_port(self, start_port: int) -> Optional[int]:
        port = start_port
        while port < start_port + 200:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(('127.0.0.1', port))
                sock.close()
                return port
            except OSError:
                port += 1
            finally:
                sock.close()
        return None
    
    def create_workers_parallel(self, config_manager, ports: List[int], max_workers: int) -> List[TorProcess]:
        successful_workers = []
        batch_size = 10
        max_retries = 3
        
        for retry in range(max_retries):
            if len(successful_workers) >= max_workers:
                break
                
            remaining_needed = max_workers - len(successful_workers)
            ports_to_try = ports[len(successful_workers):len(successful_workers) + remaining_needed + 10]
            
            if not ports_to_try:
                break
                
            logger.info(f"Attempt {retry + 1}: Creating {min(remaining_needed, len(ports_to_try))} workers (have {len(successful_workers)})")
            
            for i in range(0, len(ports_to_try), batch_size):
                batch_ports = ports_to_try[i:i + batch_size]
                
                workers_to_start = []
                for port in batch_ports:
                    worker = TorProcess(port=port, exit_nodes=[])
                    workers_to_start.append((worker, port))
                
                def start_worker(worker_data):
                    worker, port = worker_data
                    try:
                        if not worker.create_config(config_manager):
                            worker.cleanup()
                            return None
                        
                        if not worker.start_process():
                            worker.cleanup()
                            return None
                        
                        time.sleep(0.5)
                        
                        if self._wait_for_worker_startup(worker, timeout=25):
                            return worker
                        else:
                            worker.stop_process()
                            worker.cleanup()
                            return None
                    except Exception as e:
                        logger.debug(f"Worker on port {port} failed: {e}")
                        worker.cleanup()
                        return None
                
                with ThreadPoolExecutor(max_workers=len(workers_to_start)) as executor:
                    future_to_port = {executor.submit(start_worker, wd): wd[1] for wd in workers_to_start}
                    
                    for future in as_completed(future_to_port):
                        result = future.result()
                        if result:
                            successful_workers.append(result)
                            if len(successful_workers) >= max_workers:
                                break
                
                if len(successful_workers) >= max_workers:
                    break
                
                time.sleep(1)
            
            if len(successful_workers) >= max_workers:
                break
                
            logger.info(f"After attempt {retry + 1}: {len(successful_workers)} workers successfully started")
        
        return successful_workers[:max_workers]
    
    async def create_workers_async(self, config_manager, ports: List[int], max_workers: int) -> List[TorProcess]:
        semaphore = asyncio.Semaphore(self.max_concurrent)
        tasks = []
        
        for port in ports[:max_workers]:
            task = self._create_single_worker_async(semaphore, config_manager, port)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        successful_workers = [worker for worker in results if isinstance(worker, TorProcess)]
        
        return successful_workers
    
    async def _create_single_worker_async(self, semaphore: asyncio.Semaphore, config_manager, port: int) -> Optional[TorProcess]:
        async with semaphore:
            worker = TorProcess(port=port, exit_nodes=[])
            
            try:
                if not worker.create_config(config_manager):
                    worker.cleanup()
                    return None
                
                if not worker.start_process():
                    worker.cleanup()
                    return None
                
                if not await self._wait_for_worker_startup_async(worker):
                    worker.stop_process()
                    worker.cleanup()
                    return None
                
                return worker
            except Exception:
                worker.cleanup()
                return None
    
    def create_instances_with_nodes(self, config_manager, node_distributions, instances_dict, load_balancer, added_to_balancer, next_port_start):
        successful_instances = {}
        failed_ids = []
        
        if not node_distributions:
            return successful_instances, failed_ids
        
        logger.info(f"Starting parallel creation of {len(node_distributions)} Tor instances")
        
        # Подготавливаем данные для создания
        instances_to_create = []
        port_counter = next_port_start
        
        for process_id, dist_data in node_distributions.items():
            exit_nodes = dist_data.get('exit_nodes', [])
            if not exit_nodes:
                logger.warning(f"Skipping instance {process_id} due to no assigned exit nodes.")
                failed_ids.append(process_id)
                continue
            
            instances_to_create.append((process_id, port_counter, exit_nodes))
            port_counter += 1
        
        def create_single_instance(instance_data):
            process_id, port, exit_nodes = instance_data
            
            try:
                logger.info(f"[{process_id}] Starting creation of Tor process on port {port} with {len(exit_nodes)} exit nodes")
                
                instance = TorProcess(port=port, exit_nodes=exit_nodes)
                
                if not instance.create_config(config_manager):
                    logger.error(f"[{process_id}] Tor process on port {port} failed to create config")
                    return (process_id, None)
                
                if not instance.start_process():
                    logger.error(f"[{process_id}] Tor process on port {port} failed to start")
                    instance.cleanup()
                    return (process_id, None)
                
                if not self._wait_for_worker_startup(instance, timeout=30):
                    logger.error(f"[{process_id}] Tor process on port {port} failed to start properly")
                    instance.stop_process()
                    instance.cleanup()
                    return (process_id, None)
                
                instance.is_running = True
                logger.info(f"[{process_id}] Tor process on port {port} started successfully")
                
                return (process_id, (port, instance))
                
            except Exception as e:
                logger.error(f"[{process_id}] Error creating instance: {e}")
                return (process_id, None)
        
        # Создаем инстансы параллельно
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as executor:
            future_to_id = {executor.submit(create_single_instance, data): data[0] for data in instances_to_create}
            
            for future in as_completed(future_to_id):
                process_id, result = future.result()
                
                if result:
                    port, instance = result
                    successful_instances[port] = instance
                    instances_dict[port] = instance
                    load_balancer.add_proxy(port)
                    added_to_balancer.add(port)
                else:
                    failed_ids.append(process_id)
        
        successful_count = len(successful_instances)
        logger.info(f"Parallel instance creation completed: {successful_count}/{len(node_distributions)} successful, {len(failed_ids)} failed")
        
        return successful_instances, failed_ids

    def _get_next_port(self):
        return self.port_start + int(time.time() * 1000) % 10000
    
    def _wait_for_worker_startup(self, worker: TorProcess, timeout: int = 15) -> bool:
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if worker.process and worker.process.poll() is not None:
                return False
            
            try:
                if worker.test_connection():
                    return True
            except Exception:
                pass
            
            time.sleep(0.1)
        
        return False
    
    async def _wait_for_worker_startup_async(self, worker: TorProcess, timeout: int = 15) -> bool:
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if worker.process and worker.process.poll() is not None:
                return False
            
            try:
                if worker.test_connection():
                    return True
            except Exception:
                pass
            
            await asyncio.sleep(0.1)
        
        return False
