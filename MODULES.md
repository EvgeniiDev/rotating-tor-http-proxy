# Module Overview

This document describes each module in the project and their relationships.

---

## config_manager.py
Contains TorConfigBuilder class following unified config builder pattern: generates Tor configuration files with optional exit nodes, manages config directories, provides file writing and cleanup methods. Follows same API pattern as PolipoConfigBuilder and HAProxyConfigBuilder.

## utils.py
Common utility functions (e.g., safe thread shutdown). Used by pool manager for cleanup.

## tor_process.py
Advanced Tor process wrapper: manages single process lifecycle, health checks, tracks exit IP and supports dynamic configuration reloading via SIGHUP signal. Provides `reconfigure()` method for hot-swapping exit nodes without process restart. Used by pool manager and exit node tester for efficient process management.

## tor_relay_manager.py
Fetches Tor relay lists, extracts exit node IPs, and computes distributions for instances. Supplies exit nodes to pool manager and redistributor.

## http_load_balancer.py
Implements HTTP proxy load balancer interface: `add_proxy`, `remove_proxy`, request routing. Integrated with pool manager.

## tor_pool_manager.py
Coordinates a pool of Tor processes with intelligent resource management: startup, shutdown, cleanup, stats, health monitoring, hot-reload of exit_nodes, and seamless integration with load balancer. Uses reconfigurable exit node testing for efficient node validation and replacement.

## exit_node_tester.py
High-performance exit node testing and filtering system: implements reconfigurable approach using process pools and SIGHUP for rapid node validation. Features parallel testing with resource optimization (4x less memory, 2x faster than traditional approaches), HTTP status validation, and Steam Community Market endpoint testing. Provides intelligent chunking and load distribution across reusable Tor instances.

## main.py
Application entry point: initializes configuration, relay manager, load balancer, pool manager with reconfigurable exit node testing; orchestrates the complete system startup and monitors until shutdown. Configures exit node checker with config builder for optimal performance.

## tor_parallel_runner.py
Manages parallel execution of multiple Tor instances: provides concurrent startup, monitoring, and lifecycle management for up to 20 Tor processes. Integrates with tor_pool_manager for scalable proxy pool operations.

## polipo_config_builder.py
Creates Polipo configuration files for SOCKS to HTTP conversion following unified config builder pattern. Generates proxy configs that connect to Tor SOCKS ports and expose HTTP endpoints for each Tor instance. Provides build_config, write_config_file and cleanup_config_files methods.

## haproxy_config_builder.py
Generates HAProxy load balancer configuration following unified config builder pattern. Creates unified HTTP proxy endpoint that distributes requests across multiple Polipo instances using round-robin load balancing. Provides build_config, write_config_file and cleanup_config_file methods.

## proxy_manager.py
Proxy system manager: orchestrates Tor relay fetching using TorRelayManager, generates configurations using TorConfigBuilder, manages Polipo and HAProxy services, provides health monitoring and graceful shutdown.

## main.py
Entry point for proxy system. Initializes ProxyManager, handles signals, provides status monitoring and cleanup operations.

## test_proxy_system.py
Test script for proxy system. Validates proxy functionality by making HTTP requests through the load balancer endpoint.

---

# Module Relationships

```plaintext
 main.py
    ├ config_manager.py
    ├ tor_relay_manager.py
    ├ http_load_balancer.py
    └ tor_pool_manager.py
           ├ tor_process.py (with reconfigure method)
           ├ tor_parallel_runner.py
           ├ exit_node_tester.py (reconfigurable approach)
           └ utils.py

 main.py
    └ proxy_manager.py
           ├ tor_relay_manager.py (reused)
           ├ config_manager.py (TorConfigBuilder reused)
           ├ polipo_config_builder.py (new)
           └ haproxy_config_builder.py (new)
```

- `main.py` wires up all components with optimized configuration.
- `tor_pool_manager` orchestrates the entire system using specialized components.
- `tor_parallel_runner` manages concurrent Tor process execution.
- `exit_node_tester` implements resource-efficient parallel testing using process pools and SIGHUP reconfiguration.
- `tor_process` provides advanced process control with hot-reload capabilities via `reconfigure()` method.
- `utils` provides helpers for thread and process management across modules.
- `main.py` provides system entry point and signal handling.
- `proxy_manager.py` orchestrates the complete proxy system using reused and new components.
- `polipo_config_builder.py` and `haproxy_config_builder.py` provide new HTTP proxy and load balancing capabilities.