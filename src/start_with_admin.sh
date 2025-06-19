#!/bin/bash

function log() {
    if [[ $# == 1 ]]; then
        level="info"
        msg=$1
    elif [[ $# == 2 ]]; then
        level=$1
        msg=$2
    fi
    echo "$(date -u +"%Y-%m-%dT%H:%M:%SZ") [controller] [${level}] ${msg}"
}

# Start admin panel with HTTP balancer in background
log "Starting Tor HTTP Proxy Admin Panel with integrated load balancer..."
python3 start_new.py &
ADMIN_PANEL_PID=$!

# Function to handle shutdown
cleanup() {
    log "Shutting down..."
    if [[ -n $ADMIN_PANEL_PID ]]; then
        kill $ADMIN_PANEL_PID 2>/dev/null
    fi
    
    # Kill all tor processes
    pkill -f tor 2>/dev/null
    
    log "All services stopped"
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

log "Initializing HTTP Load Balancer configuration..."

# Create necessary directories for Tor
mkdir -p /var/lib/tor/data
chmod 755 /var/lib/tor/data

# Create /etc/tor directory if it doesn't exist
mkdir -p /etc/tor
chmod 755 /etc/tor

log "HTTP Load Balancer started successfully"
log "Admin panel available at http://localhost:5000"
log "HTTP proxy available at http://localhost:8080"

# Wait for admin panel to finish
wait $ADMIN_PANEL_PID
