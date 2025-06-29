#!/bin/bash

set -e

PROJECT_DIR="$HOME/tor-http-proxy"
SERVICE_NAME="tor-http-proxy"
USER="$USER"
TOR_PROCESSES=200

if [[ $EUID -ne 0 ]]; then
   echo "This script must be run as root (use sudo)" 
   exit 1
fi

echo "Updating system..."
apt update && apt upgrade -y

echo "Installing dependencies..."
apt install -y python3 python3-pip python3-venv tor git

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
mkdir -p "$PROJECT_DIR/data"
chmod 755 "$PROJECT_DIR/data"

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
ExecStart=$PROJECT_DIR/venv/bin/python $PROJECT_DIR/main.py
MemoryAccounting=yes
MemoryHigh=4.3G
MemoryMax=4.5G
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
echo ""
echo "Start now? (y/n)"
read -r response
if [[ "$response" =~ ^[Yy]$ ]]; then
    systemctl start $SERVICE_NAME
    echo "Service started!"
    echo "Check status: sudo systemctl status $SERVICE_NAME"
fi
