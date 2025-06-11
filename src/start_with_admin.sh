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

# Start admin panel in background
log "Starting Tor Network Admin Panel..."
python3 admin_panel.py &
ADMIN_PANEL_PID=$!

# Function to handle shutdown
cleanup() {
    log "Shutting down..."
    if [[ -n $ADMIN_PANEL_PID ]]; then
        kill $ADMIN_PANEL_PID 2>/dev/null
    fi
    # Kill all tor processes
    pkill -f tor 2>/dev/null

    # Stop HAProxy using systemd
    log "Stopping HAProxy..."
    systemctl stop haproxy 2>/dev/null || true
    
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

log "Initializing configuration files..."


systemctl start haproxy
if ! systemctl is-active --quiet haproxy; then
    log "error" "Failed to start HAProxy with systemd"
    exit 1
fi

log "Base services started successfully!"
log "Admin Panel: http://localhost:5000"
log "HAProxy Stats: http://localhost:4444"
log "Proxy will be available at: socks5://localhost:1080 (after starting Tor instances through Admin Panel)"
log "Use the Admin Panel to start and manage Tor instances"

# Wait for admin panel or any process to exit
wait

# Cleanup on exit
cleanup
