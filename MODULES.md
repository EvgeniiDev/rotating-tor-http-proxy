# Module Overview

This document describes each module in the project and their relationships.

---

## config_manager.py
Loads and validates configuration settings (files or environment). Provides parameters consumed by all other modules.

## utils.py
Common utility functions (e.g., safe thread shutdown). Used by pool manager for cleanup.

## tor_process.py
Lightweight Tor process wrapper: manages single process lifecycle, health checks, tracks exit IP and supports configuration reloading. Used by pool manager for process management.

## tor_relay_manager.py
Fetches Tor relay lists, extracts exit node IPs, and computes distributions for instances. Supplies exit nodes to pool manager and redistributor.

## http_load_balancer.py
Implements HTTP proxy load balancer interface: `add_proxy`, `remove_proxy`, request routing. Integrated with pool manager.

## tor_pool_manager.py
Coordinates a pool of Tor processes using single-threaded monitoring: startup, shutdown, cleanup, stats, health monitoring, hot-reload of exit_nodes, and integration with load balancer.

## main.py
Application entry point: initializes configuration, relay manager, load balancer, pool manager; starts the pool and monitors until shutdown.

---

# Module Relationships

```plaintext
 main.py
    ├ config_manager.py
    ├ tor_relay_manager.py
    ├ http_load_balancer.py
    └ tor_pool_manager.py
           ├ tor_process.py
           └ utils.py
```

- `main.py` wires up all components.
- `tor_pool_manager` uses `TorProcess` to manage individual processes with unified monitoring.
- `utils` provides helpers for thread and process management across modules.
