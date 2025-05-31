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
python3 /admin_panel.py &
ADMIN_PANEL_PID=$!

# Function to handle shutdown
cleanup() {
    log "Shutting down..."
    if [[ -n $ADMIN_PANEL_PID ]]; then
        kill $ADMIN_PANEL_PID 2>/dev/null
    fi
    # Kill all tor and privoxy processes
    pkill -f tor 2>/dev/null
    pkill -f privoxy 2>/dev/null
    pkill -f haproxy 2>/dev/null
    exit 0
}

# Set up signal handlers
trap cleanup SIGINT SIGTERM

if ((TOR_INSTANCES < 1 || TOR_INSTANCES > 40)); then
    log "fatal" "Environment variable TOR_INSTANCES has to be within the range of 1...40"
    exit 1
fi

if ((TOR_REBUILD_INTERVAL < 600)); then
    log "fatal" "Environment variable TOR_REBUILD_INTERVAL has to be bigger than 600 seconds"
    # otherwise AWS may complain about it, because http://checkip.amazonaws.com is asked too often
    exit 2
fi

base_tor_socks_port=10000
base_tor_ctrl_port=20000
base_http_port=30000

log "Initializing configuration files..."

# "reset" the HAProxy config file because it may contain the previous Privoxy instances information from the previous docker run
cp /etc/haproxy/haproxy.cfg.default /etc/haproxy/haproxy.cfg
# same "reset" logic as above
cp /etc/tor/torrc.default /etc/tor/torrc

# Ensure proper permissions for tmp directory (use user's home tmp)
export TMPDIR=/home/proxy/tmp
mkdir -p "$TMPDIR"

if [[ -n $TOR_EXIT_COUNTRY ]]; then
    IFS=', ' read -r -a countries <<< "$TOR_EXIT_COUNTRY"
    value=""
    is_first=1
    for country in "${countries[@]}"
    do
        country=$(xargs <<< "$country")
        length=${#country}
        if [[ $length -ne 2 ]]; then
            continue
        fi
        if [[ $is_first -ne 1 ]]; then
            value="$value,"
        else
            is_first=0
        fi
        value="$value{$country}"
    done
    if [[ -n $value ]]; then
        echo "ExitNodes $value" >> /etc/tor/torrc
        log "Setting ExitNodes to $value"
    fi
fi

log "Configuration files initialized. Tor instances will be managed through the Admin Panel."

# Start HAProxy with empty backend configuration (will be populated by admin panel)
log "Starting HAProxy load balancer..."
haproxy -f /etc/haproxy/haproxy.cfg &
HAPROXY_PID=$!

log "Base services started successfully!"
log "Admin Panel: http://localhost:5000"
log "Proxy will be available at: http://localhost:3128 (after starting Tor instances through Admin Panel)"
log "Use the Admin Panel to start and manage Tor instances"

# Wait for admin panel or any process to exit
wait

# Cleanup on exit
cleanup
