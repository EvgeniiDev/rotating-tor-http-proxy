#!/bin/bash

set -e

PROJECT_DIR="/opt/tor-http-proxy"
SERVICE_NAME="tor-http-proxy"
USER="tor-proxy"
TOR_PROCESSES=200

echo "=== Installing Tor HTTP Proxy on Ubuntu 22.04 ==="

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)" 
   exit 1
fi

echo "Updating system..."
apt update && apt upgrade -y

echo "Installing dependencies..."
apt install -y python3 python3-pip python3-venv tor git

echo "Creating service user..."
if ! id "$USER" &>/dev/null; then
    useradd -r -s /bin/false -d "$PROJECT_DIR" "$USER"
fi

echo "Creating project directory..."
mkdir -p "$PROJECT_DIR"

echo "Copying project files..."
cp -r src/* "$PROJECT_DIR/"

echo "Creating virtual environment..."
cd "$PROJECT_DIR"
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo "Setting up Tor directories..."
mkdir -p "$PROJECT_DIR/.tor_proxy/config"
mkdir -p "$PROJECT_DIR/.tor_proxy/data"
mkdir -p "$PROJECT_DIR/.tor_proxy/logs"
chmod 755 "$PROJECT_DIR/.tor_proxy/config"
chmod 755 "$PROJECT_DIR/.tor_proxy/data"
chmod 755 "$PROJECT_DIR/.tor_proxy/logs"

echo "Setting up permissions..."
chown -R "$USER:$USER" "$PROJECT_DIR"

echo "Creating systemd service..."
cat > /etc/systemd/system/$SERVICE_NAME.service << EOF
[Unit]
Description=Tor HTTP Proxy with Load Balancer

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PYTHONPATH=$PROJECT_DIR
Environment=TOR_PROCESSES=$TOR_PROCESSES
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/start_new.py
Restart=always
RestartSec=10
MemoryAccounting=yes
MemoryMax=4G
CPUAccounting=yes
CPUQuota=200%

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
echo "  HTTP Proxy: http://localhost:8080"
echo "  Admin Panel: http://localhost:5000"
echo ""
echo "Start now? (y/n)"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    systemctl start $SERVICE_NAME
    echo "Service started!"
    echo "Check status: sudo systemctl status $SERVICE_NAME"
fi
