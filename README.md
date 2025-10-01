# Rotating Tor HTTP Proxy

A high-performance orchestrator that manages a pool of Tor processes with mitmproxy load balancing.

## Key Components

1. **Tor Process Manager** - Handles lifecycle of multiple isolated Tor instances
2. **mitmproxy Balancer** - Provides HTTP proxy with intelligent load balancing across Tor SOCKS proxies
3. **Relay Manager** - Fetches and distributes exit nodes from Onionoo directory
4. **Health Monitor** - Performs continuous health checks and auto-restarts failed instances

## Core Functionality

- **Pool Management**: Runs up to 400 concurrent Tor instances with automatic port allocation
- **Load Balancing**: Distributes requests across healthy Tor proxies with round-robin and retry logic
- **Exit Node Control**: Configures specific exit nodes for each Tor instance
- **Auto Recovery**: Monitors health and automatically restarts failed Tor processes
- **Circuit Rotation**: Supports NEWNYM signal for IP rotation

## Working Algorithms

### 1. Tor Pool Initialization
1. Calculate required port range based on instance count
2. Fetch exit nodes from Onionoo relay directory if configured
3. Distribute exit nodes across instances
4. Launch Tor processes in parallel batches
5. Wait for each process to become ready (SOCKS port responsive)
6. Start mitmproxy with all healthy SOCKS endpoints

### 2. Load Balancing Algorithm
1. Maintain a pool of SOCKS5 proxy endpoints with health status
2. Round-robin selection of available proxies for each request
3. Automatic retry mechanism with fallback to alternative proxies
4. Cooldown period for failed proxies to prevent cascading failures
5. Success-based health tracking to prioritize reliable proxies

### 3. Health Monitoring
1. Periodic background health checks to all Tor instances
2. HTTP request to configured endpoint through each proxy
3. Mark failed instances for restart
4. Automatic restart of unresponsive processes
5. Dynamic mitmproxy configuration update when topology changes

### 4. Failure Recovery
1. Detection of failed Tor processes
2. Graceful shutdown of failed instances
3. Immediate restart with same configuration
4. Re-integration into proxy pool upon successful startup

## Quick Start

```bash
# Install system dependencies
sudo apt install tor python

# Install Python package
pip install -e .

# Configure (optional)
cp .env.example .env
# Edit TOR_PROXY_TOR_INSTANCES and other settings

# Run with 20 Tor instances
python -m rotating_tor_proxy.main --tor-instances 20
```

## Usage

Once running, the proxy exposes:
- HTTP proxy: `http://127.0.0.1:8080` (mitmproxy load balancer)

Test with curl:
```bash
# Via HTTP (load balanced)
curl -x http://127.0.0.1:8080 https://httpbin.org/ip
```

## Configuration

Key environment variables:
- `TOR_PROXY_TOR_INSTANCES` - Number of Tor workers (default: 20)
- `TOR_PROXY_FRONTEND_PORT` - SOCKS5 proxy port (default: 9999)
- `TOR_PROXY_EXIT_NODES_PER_INSTANCE` - Exit nodes per instance (default: 0)
- `TOR_PROXY_HEALTH_CHECK_URL` - Health check endpoint (default: https://httpbin.org/ip)

Set via `.env` file or environment variables.