#!/bin/bash

set -e

RUN_USER=${SUDO_USER:-$USER}
USER_HOME=$(getent passwd "$RUN_USER" | cut -d: -f6)
if [[ -z "$USER_HOME" ]]; then
    USER_HOME=$(eval echo "~$RUN_USER")
fi
PROJECT_DIR="$USER_HOME/tor-http-proxy"
SERVICE_NAME="tor-http-proxy"
SERVICE_USER="$RUN_USER"
TOR_PROCESSES=50
FRONTEND_PORT=9999
STATS_PORT=8404
HAPROXY_CFG_PATH="/etc/haproxy/haproxy.cfg"

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)" 
   exit 1
fi

echo "Installing dependencies..."
apt install -y python3 python3-pip python3-venv tor git haproxy

echo "Creating project directory..."
mkdir -p "$PROJECT_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$PROJECT_DIR"

echo "Copying project files..."
cp -r src/* "$PROJECT_DIR/"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$PROJECT_DIR"

echo "Creating virtual environment..."
cd "$PROJECT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
deactivate
chown -R "$SERVICE_USER":"$SERVICE_USER" "$PROJECT_DIR"

echo "Setting up Tor directories..."
mkdir -p "$PROJECT_DIR/data"
chmod 755 "$PROJECT_DIR/data"

SYSTEMCTL_PATH=$(command -v systemctl)

echo "Configuring HAProxy..."
if [[ -f "$HAPROXY_CFG_PATH" ]]; then
    cp "$HAPROXY_CFG_PATH" "$HAPROXY_CFG_PATH.bak.$(date +%s)"
fi

cat > "$HAPROXY_CFG_PATH" << EOF
global
    log /dev/log local0 info
    maxconn 4000
    user haproxy
    group haproxy
    stats timeout 30s

defaults
    mode tcp
    log global
    option tcplog
    timeout connect 10s
    timeout client 60s
    timeout server 60s
    balance roundrobin
    option tcp-check

frontend tor_socks5_frontend
    bind *:${FRONTEND_PORT}
    mode tcp
    default_backend tor_socks5_backend

frontend haproxy_stats
    bind *:${STATS_PORT}
    mode http
    stats enable
    stats uri /stats
    stats refresh 30s
    stats admin if TRUE
    stats show-legends
    stats show-desc "HAProxy Tor Pool Load Balancer"

backend tor_socks5_backend
    mode tcp
    balance roundrobin
    option tcp-check
    # Серверы будут добавлены динамически менеджером пула
EOF

chown "$SERVICE_USER":"$SERVICE_USER" "$HAPROXY_CFG_PATH"
chmod 644 "$HAPROXY_CFG_PATH"

echo "Allowing $SERVICE_USER to reload HAProxy via sudo without password"
SUDOERS_FILE="/etc/sudoers.d/tor-http-proxy"
cat > "$SUDOERS_FILE" << EOF
$SERVICE_USER ALL=NOPASSWD:$SYSTEMCTL_PATH reload haproxy,$SYSTEMCTL_PATH is-active haproxy
EOF
chmod 440 "$SUDOERS_FILE"

systemctl daemon-reload
systemctl enable --now haproxy
systemctl restart haproxy

echo "Creating systemd service..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Tor HTTP Proxy with Load Balancer

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONPATH=$PROJECT_DIR
Environment=TOR_PROCESSES=$TOR_PROCESSES
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/main.py
MemoryAccounting=yes
MemoryHigh=7.5G
MemoryMax=7.8G
CPUAccounting=yes
CPUQuota=300%

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd and enabling service..."
systemctl daemon-reload
systemctl enable $SERVICE_NAME

echo "=== Installation completed! ==="
echo ""
echo "Service management:"
echo "  sudo systemctl start $SERVICE_NAME    # Start"
echo "  sudo systemctl stop $SERVICE_NAME     # Stop"
echo "  sudo systemctl restart $SERVICE_NAME  # Restart"
echo "  sudo systemctl status $SERVICE_NAME   # Status"
echo "  journalctl -u $SERVICE_NAME -f        # Logs"
echo ""
echo "After starting the service:"
echo "  SOCKS5 Proxy: 127.0.0.1:$FRONTEND_PORT"
echo "  HAProxy stats: http://127.0.0.1:$STATS_PORT/stats"
echo ""
echo "Start now? (y/n)"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    systemctl start $SERVICE_NAME
    echo "Service started!"
    echo "Check status: sudo systemctl status $SERVICE_NAME"
fi
