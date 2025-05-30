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

log "Start creating a pool of ${TOR_INSTANCES} tor instances..."

# "reset" the HAProxy config file because it may contain the previous Privoxy instances information from the previous docker run
cp /etc/haproxy/haproxy.cfg.default /etc/haproxy/haproxy.cfg
# same "reset" logic as above
cp /etc/tor/torrc.default /etc/tor/torrc

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

for ((i=1; i<=TOR_INSTANCES; i++))
do
    tor_socks_port=$((base_tor_socks_port + i))
    tor_ctrl_port=$((base_tor_ctrl_port + i))
    http_port=$((base_http_port + i))

    tor_dir="/var/local/tor/${i}"
    privoxy_dir="/var/local/privoxy/${i}"

    mkdir -p "${tor_dir}"
    mkdir -p "${privoxy_dir}"

    tor_ctrl_password="tor-password-${i}"
    tor_ctrl_password_hash=$(tor --hash-password "${tor_ctrl_password}")

    log "Starting instance ${i}..."

    # Create Tor configuration for this instance
    tor_config_file="/etc/tor/torrc.${i}"
    cp /etc/tor/torrc "${tor_config_file}"
    cat >> "${tor_config_file}" << EOF
SocksPort ${tor_socks_port}
ControlPort ${tor_ctrl_port}
HashedControlPassword ${tor_ctrl_password_hash}
DataDirectory ${tor_dir}
PidFile ${tor_dir}/tor.pid
Log notice file ${tor_dir}/tor.log
EOF

    # Start Tor instance
    tor -f "${tor_config_file}" &
    tor_pid=$!
    echo "${tor_pid}" > "${tor_dir}/tor.pid"

    # Create Privoxy configuration for this instance
    privoxy_config_file="/etc/privoxy/config.${i}"
    sed "s|PRIVOXY_DIR|${privoxy_dir}|g; s|HTTP_PORT|${http_port}|g; s|TOR_SOCKS_PORT|${tor_socks_port}|g" /etc/privoxy/config.templ > "${privoxy_config_file}"

    # Start Privoxy instance
    privoxy --no-daemon "${privoxy_config_file}" &
    privoxy_pid=$!
    echo "${privoxy_pid}" > "${privoxy_dir}/privoxy.pid"

    # Add to HAProxy configuration
    cat >> /etc/haproxy/haproxy.cfg << EOF
    server tor${i} 127.0.0.1:${http_port} check
EOF

    log "Instance ${i} started: Tor SOCKS=${tor_socks_port}, Control=${tor_ctrl_port}, HTTP=${http_port}"
done

# Start HAProxy
log "Starting HAProxy load balancer..."
haproxy -f /etc/haproxy/haproxy.cfg &
HAPROXY_PID=$!

log "All services started successfully!"
log "Admin Panel: http://localhost:5000"
log "Proxy available at: http://localhost:3128"

# Function to rebuild Tor circuits periodically
rebuild_circuits() {
    while true; do
        sleep ${TOR_REBUILD_INTERVAL}
        log "Rebuilding Tor circuits..."
        
        for ((i=1; i<=TOR_INSTANCES; i++))
        do
            tor_ctrl_port=$((base_tor_ctrl_port + i))
            tor_ctrl_password="tor-password-${i}"
            
            # Send NEWNYM signal to rebuild circuit
            echo -e "AUTHENTICATE \"${tor_ctrl_password}\"\nSIGNAL NEWNYM\nQUIT" | \
                nc 127.0.0.1 ${tor_ctrl_port} > /dev/null 2>&1
        done
        
        log "Circuits rebuilt for all ${TOR_INSTANCES} instances"
    done
}

# Start circuit rebuilding in background
rebuild_circuits &
REBUILD_PID=$!

# Wait for any process to exit
wait

# Cleanup on exit
cleanup
