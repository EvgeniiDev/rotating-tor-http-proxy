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



# Set up Tor configuration directories in user home
echo "Setting up Tor configuration directories..."

# Создаем основную структуру директорий
USER_HOME=$(eval echo ~$USER)
TOR_PROXY_DIR="$USER_HOME/.tor_proxy"

echo "Creating Tor proxy directories in: $TOR_PROXY_DIR"
mkdir -p "$TOR_PROXY_DIR/config"
mkdir -p "$TOR_PROXY_DIR/data"
mkdir -p "$TOR_PROXY_DIR/logs"

# Устанавливаем правильные права доступа
chmod 755 "$TOR_PROXY_DIR"
chmod 755 "$TOR_PROXY_DIR/config"
chmod 755 "$TOR_PROXY_DIR/data"
chmod 755 "$TOR_PROXY_DIR/logs"

# Создаем базовый файл конфигурации для проверки системы
cat > "$TOR_PROXY_DIR/config/.gitkeep" << 'EOF'
# This file ensures the config directory is preserved in git
# Tor configuration files will be created here automatically
EOF

echo "✅ Tor proxy directories created successfully!"
echo "📁 Config dir: $TOR_PROXY_DIR/config"
echo "📁 Data dir: $TOR_PROXY_DIR/data"  
echo "📁 Logs dir: $TOR_PROXY_DIR/logs"

# Make scripts executable
echo "Making scripts executable..."
chmod +x start_new.py

if [ -f "src/start_with_admin.sh" ]; then
    chmod +x src/start_with_admin.sh
fi

# Создаем символические ссылки для удобства
echo "Creating convenient symlinks..."
USER_HOME=$(eval echo ~$USER)
TOR_PROXY_DIR="$USER_HOME/.tor_proxy"

# Создаем ссылку на директорию конфигурации в корне проекта для удобства разработки
if [ ! -L "tor_configs" ]; then
    ln -s "$TOR_PROXY_DIR/config" tor_configs
    echo "Created symlink: tor_configs -> $TOR_PROXY_DIR/config"
fi

# Создаем файл с информацией о путях
cat > "$TOR_PROXY_DIR/paths.txt" << EOF
# Tor Proxy Directory Structure
# Generated on $(date)

Base Directory: $TOR_PROXY_DIR
Config Directory: $TOR_PROXY_DIR/config
Data Directory: $TOR_PROXY_DIR/data
Logs Directory: $TOR_PROXY_DIR/logs

# Usage:
# Configuration files will be created as: $TOR_PROXY_DIR/config/torrc.{instance_id}
# Data directories will be created as: $TOR_PROXY_DIR/data/data_{instance_id}
# PID files will be created as: $TOR_PROXY_DIR/data/tor_{instance_id}.pid
EOF

echo "✅ Paths information saved to: $TOR_PROXY_DIR/paths.txt"

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
ProtectHome=false
ReadWritePaths=${SCRIPT_DIR}
ReadWritePaths=%h/.tor_proxy

# Environment
Environment=PYTHONPATH=${SCRIPT_DIR}/src
Environment=HOME=%h

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
