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
if [ -f "start_new.py" ]; then
    chmod +x start_new.py
fi

if [ -f "src/start_with_admin.sh" ]; then
    chmod +x src/start_with_admin.sh
fi

# Script installation completed
echo "Installation completed successfully!"
echo ""
echo "To run the application with new architecture:"
echo "python3 start_new.py"
echo ""
echo "Alternatively, to run with legacy start script:"
echo "cd src && sudo -u proxy ./start_with_admin.sh"
echo ""
echo "The following ports will be exposed:"
echo "- 5000/tcp: Admin panel web interface"
echo "- 8080/tcp: HTTP proxy (if load balancer is enabled)"
