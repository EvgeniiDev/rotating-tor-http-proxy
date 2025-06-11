#!/bin/bash

set -e  # Exit on any error

echo "Installing rotating Tor HTTP proxy on Ubuntu 24.04..."

# Update package list
echo "Updating package list..."
sudo apt update

# Install system dependencies
echo "Installing system dependencies..."
sudo apt install -y python3 python3-pip python3-venv tor socat haproxy

# Install Python packages
echo "Installing Python dependencies..."
if [ -f "src/requirements.txt" ]; then
    pip3 install --user -r src/requirements.txt --break-system-packages
else
    echo "Warning: src/requirements.txt not found, skipping Python package installation"
fi

# Create proxy user and group
echo "Setting up proxy user..."
if ! getent group proxy > /dev/null 2>&1; then
    sudo groupadd proxy
fi

if ! id -u proxy > /dev/null 2>&1; then
    sudo useradd -r -s /bin/false -g proxy -u 1000 proxy
fi

# Set up HAProxy configuration
echo "Setting up HAProxy configuration..."
# Remove default HAProxy config and copy our config
sudo rm -f /etc/haproxy/haproxy.cfg
if [ -f "src/haproxy.cfg" ]; then
    sudo cp src/haproxy.cfg /etc/haproxy/haproxy.cfg
else
    echo "Error: src/haproxy.cfg not found!"
    exit 1
fi
sudo chown proxy:proxy /etc/haproxy/haproxy.cfg
sudo mkdir -p /var/lib/haproxy
sudo chown -R proxy:proxy /var/lib/haproxy
sudo mkdir -p /var/local/haproxy
sudo chown -R proxy:proxy /var/local/haproxy
sudo touch /var/local/haproxy/server-state
sudo chown proxy:proxy /var/local/haproxy/server-state

# Set up Tor configuration
echo "Setting up Tor configuration..."
sudo touch /etc/tor/torrc
sudo chown -R proxy:proxy /etc/tor/

# Create proxy user home directory and tmp
echo "Setting up proxy user directories..."
sudo mkdir -p /home/proxy/tmp
sudo chown -R proxy:proxy /home/proxy/tmp

# Make scripts executable
echo "Making scripts executable..."
if [ -f "src/start_with_admin.sh" ]; then
    chmod +x src/start_with_admin.sh
fi

if [ -f "src/admin_panel.py" ]; then
    chmod +x src/admin_panel.py
fi

# Script installation completed
echo "Installation completed successfully!"
echo ""
echo "To run the application:"
echo "cd src && sudo -u proxy ./start_with_admin.sh"
echo ""
echo "The following ports will be exposed:"
echo "- 1080/tcp: SOCKS proxy"
echo "- 4444/tcp: HAProxy stats"
echo "- 5000/tcp: Admin panel"
