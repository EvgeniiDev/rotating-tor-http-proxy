#!/bin/bash

set -e  # Exit on any error
echo "Installing rotating Tor HTTP proxy on Ubuntu 24.04..."

# Update package list
echo "Updating package list..."
sudo apt update

# Install system dependencies
echo "Installing system dependencies..."
sudo apt install -y python3 python3-pip tor git

# Install Python packages
echo "Installing Python dependencies..."
sudo pip3 install -r src/requirements.txt --break-system-packages



# Set up Tor configuration
echo "Setting up Tor configuration..."
sudo mkdir -p /var/lib/tor
sudo mkdir -p /var/log/tor
sudo touch /etc/tor/torrc


# Make scripts executable
echo "Making scripts executable..."
chmod +x start_new.py
chmod +x service_control.sh

if [ -f "src/start_with_admin.sh" ]; then
    chmod +x src/start_with_admin.sh
fi

# Create systemd service
echo "Creating systemd service..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

sudo tee /etc/systemd/system/tor-proxy.service > /dev/null <<EOF
[Unit]
Description=Rotating Tor HTTP Proxy
After=network.target
Wants=network.target

[Service]
Type=simple
WorkingDirectory=${SCRIPT_DIR}
ExecStart=/usr/bin/python3 ${SCRIPT_DIR}/start_new.py
Restart=always
RestartSec=10
KillMode=mixed
KillSignal=SIGTERM
TimeoutStopSec=30

# Memory limits (4GB total for service and all children)
MemoryAccounting=true
MemoryMax=4G
MemoryHigh=3.5G

# Additional security
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${SCRIPT_DIR}
ReadWritePaths=/home/proxy
ReadWritePaths=/var/lib/tor
ReadWritePaths=/var/log/tor
ReadWritePaths=/etc/tor

# Environment
Environment=PYTHONPATH=${SCRIPT_DIR}/src
Environment=HOME=/home/proxy

[Install]
WantedBy=multi-user.target
EOF

# Set proper ownership for the project directory
echo "Setting up project directory permissions..."
sudo chmod -R 755 "${SCRIPT_DIR}"

# Reload systemd and enable service
echo "Enabling systemd service..."
sudo systemctl daemon-reload
sudo systemctl enable tor-proxy.service

# Script installation completed
echo "Installation completed successfully!"
echo ""
echo "Systemd service 'tor-proxy' has been created and enabled."
echo ""
echo "To control the service:"
echo "sudo systemctl start tor-proxy    # Start the service"
echo "sudo systemctl stop tor-proxy     # Stop the service"
echo "sudo systemctl restart tor-proxy  # Restart the service"
echo "sudo systemctl status tor-proxy   # Check service status"
echo "journalctl -u tor-proxy -f        # View logs"
echo ""
echo "The following ports will be exposed:"
echo "- 5000/tcp: Admin panel web interface"
echo "- 8080/tcp: HTTP proxy (if load balancer is enabled)"
echo ""
echo "Memory usage is limited to 4GB for the service and all child processes."
