#!/bin/bash

# Example startup script for Tor HTTP Proxy

set -e

echo "=== Tor HTTP Proxy Startup ==="
echo ""

# Check dependencies
echo "Checking dependencies..."

if ! command -v tor &> /dev/null; then
    echo "Error: tor is not installed"
    echo "Install with: sudo apt-get install tor"
    exit 1
fi

if ! command -v polipo &> /dev/null; then
    echo "Error: polipo is not installed" 
    echo "Install with: sudo apt-get install polipo"
    exit 1
fi

if ! command -v haproxy &> /dev/null; then
    echo "Error: haproxy is not installed"
    echo "Install with: sudo apt-get install haproxy" 
    exit 1
fi

if ! python3 -c "import requests" &> /dev/null; then
    echo "Error: requests module is not installed"
    echo "Install with: pip3 install requests"
    exit 1
fi

echo "All dependencies are available"
echo ""

# Set number of proxies
PROXIES=${1:-5}
echo "Starting $PROXIES proxy instances..."
echo ""

# Run the system
cd "$(dirname "$0")/src"
python3 main.py $PROXIES
