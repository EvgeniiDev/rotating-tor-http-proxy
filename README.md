# Rotating Tor HTTP Proxy v2

High-performance orchestrator for a pool of Tor processes fronted by mitmproxy. The
system creates isolated Tor instances with unique exit nodes, keeps them healthy,
configures mitmproxy for smart load balancing with retry logic

## Features
- Parallel launch of up to 400 Tor instances with automatic port allocation
- Exit node discovery from Onionoo and even distribution across the pool
- mitmproxy configuration generation (HTTP proxy :8080)
- Passive and active health checks with automatic restarts on failure
- Graceful shutdown, resource cleanup, and systemd service template
- Structured logging, environment-driven configuration, and test coverage

## Requirements
- Linux host with `tor` package (`scripts/install_dependencies.sh`)
- Python 3.12+
- Optional: `systemd` for service management

## Installation

### System Dependencies
First, install the required system packages:
```bash
# Install Tor
sudo apt update
sudo apt install -y tor
```

### Python Dependencies
The project uses a modern Python package structure. You can install it in two ways:

1. **Direct installation** (recommended for production):
```bash
# Install the package
pip install -e .
```

## Quick Start
```bash
# Configure environment
cp .env.example .env
# adjust TOR_PROXY_* values as needed

# Launch the orchestrator
python -m rotating_tor_proxy.main --tor-instances 20
```

The service binds mitmproxy on port 8080.

## Configuration
All settings are exposed through environment variables (prefix `TOR_PROXY_`). Key
options include:

- `TOR_PROXY_TOR_INSTANCES` – number of Tor workers (≤ 400)
- `TOR_PROXY_TOR_BASE_PORT` – starting port for SOCKS/Control allocation (default 10000)
- `TOR_PROXY_TOR_START_BATCH` – max parallel startups per batch (default 20)
- `TOR_PROXY_TOR_MAX_PORT` – upper bound for allocated SOCKS/Control ports (default 10799)
- `TOR_PROXY_EXIT_NODES_PER_INSTANCE` – fixed number of exit nodes per Tor instance
- `TOR_PROXY_EXIT_NODES_MAX` – global cap on exit nodes fetched
- `TOR_PROXY_HEALTH_CHECK_URL` – URL fetched via each Tor circuit (default httpbin)
- `TOR_PROXY_HEALTH_INTERVAL_SECONDS` – cadence for background health sampling
- `TOR_PROXY_LOG_LEVEL` – `DEBUG`, `INFO`, etc.

Adjust `.env` to tune these values. Relative paths resolve against the current
working directory.

## Monitoring & Operations
- **Runtime Stats API**: use `TorProxyIntegrator.get_stats()` (see unit tests for
parsing) if integrating with external dashboards.
- **Logs**: structured via Python logging; enable verbose traces using
`TOR_PROXY_LOG_VERBOSE=true`.

Scheduled health cycles run in a background thread. Failing Tor workers are
immediately restarted and mitmproxy config is refreshed when topology changes.

## Systemd Integration
A service unit template is provided at `systemd/rotating-tor-http-proxy.service`.
We've enhanced the service with CPU and memory limits for better resource management.

# Install Python package
cd /opt/rotating-tor-http-proxy
sudo -u torproxy pip install -e .

Logs stream through `journalctl -u rotating-tor-http-proxy`.

## Testing & Tooling
```bash
make test
make lint
```

The `Makefile` wraps common actions (`make install`, `make lint`, `make test`, `make run`).

## Project Layout
- `src/config_manager.py` – Configuration management and validation
- `src/tor_process.py` – Tor instance lifecycle management
- `src/tor_parallel_runner.py` – Concurrent orchestration of Tor instances
- `src/tor_relay_manager.py` – Onionoo integration for exit node discovery
- `src/mitmproxy_pool_manager.py` – mitmproxy configuration & launch
- `src/tor_proxy_integrator.py` – High-level coordinator
- `src/main.py` – Main entry point
- `src/exceptions.py` – Custom exception definitions
- `src/logging_utils.py` – Logging configuration
- `src/utils.py` – Utility functions
- `src/mitm_addon/` – mitmproxy balancer addon implementation

## Safety Notes
- Run under a dedicated user with minimal privileges (`torproxy` suggested)
- Ensure `tor` binary is present; otherwise reload/validation steps
are skipped with warnings
- Monitor memory usage (~40 MB per Tor process per spec) and adjust instance count accordingly
- The systemd service includes CPU and memory limits to prevent resource exhaustion