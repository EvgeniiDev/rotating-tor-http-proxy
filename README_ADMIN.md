# Tor Rotating HTTP Proxy with Admin Panel

Rotating Tor HTTP proxy with real-time subnet management capabilities.

## Features

- **Multiple Tor instances** for high availability and load balancing
- **Real-time admin panel** for network management
- **Subnet-based exit node control** with geographic filtering
- **Live monitoring** of Tor relay networks
- **WebSocket-based updates** for real-time UI
- **Individual subnet limits** and controls

## Quick Start

### Using Docker Compose (Recommended)

```bash
# Clone the repository
git clone <repository-url>
cd rotating-tor-http-proxy

# Start with admin panel
docker-compose up -d

# Access the admin panel
open http://localhost:5000
```

### Using Docker

```bash
# Build the image
docker build -t tor-proxy-admin .

# Run the container
docker run -d \
  --name tor-proxy-admin \
  -p 3128:3128 \
  -p 5000:5000 \
  -e TOR_INSTANCES=10 \
  -e TOR_REBUILD_INTERVAL=1800 \
  tor-proxy-admin
```

## Admin Panel

The admin panel provides real-time management of Tor exit networks:

### Access
- **URL**: http://localhost:5000
- **Real-time updates**: WebSocket connection for live data
- **Mobile responsive**: Works on desktop and mobile devices

### Features

#### 📊 Network Statistics Dashboard
- Total active Tor relays
- Active/blocked subnet counts
- Real-time connection status
- Last update timestamps

#### 🌍 Subnet Management
- **Enable/Disable Subnets**: Toggle entire /16 networks on/off
- **Address Limits**: Set maximum addresses per subnet
- **Geographic Info**: View countries for each subnet
- **Search & Filter**: Find specific subnets quickly

#### ⚡ Quick Actions
- **Enable All**: Activate all available subnets
- **Disable All**: Block all subnets
- **Refresh Data**: Force update relay information

#### 🔄 Real-time Updates
- Automatic data refresh every 30 seconds
- Instant UI updates via WebSocket
- Live connection status indicator

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TOR_INSTANCES` | 10 | Number of Tor instances (1-40) |
| `TOR_REBUILD_INTERVAL` | 1800 | Circuit rebuild interval (seconds) |
| `TOR_EXIT_COUNTRY` | - | Comma-separated country codes (e.g., "US,GB,DE") |

## Exposed Ports

| Port | Service | Description |
|------|---------|-------------|
| 3128 | HTTP Proxy | Main proxy endpoint |
| 4444 | HAProxy Stats | Load balancer statistics |
| 5000 | Admin Panel | Web management interface |

## Usage Examples

### Basic Proxy Usage
```bash
# Use the proxy for HTTP requests
curl --proxy http://localhost:3128 http://httpbin.org/ip

# Use with applications
export http_proxy=http://localhost:3128
export https_proxy=http://localhost:3128
```

### Admin Panel API

The admin panel exposes a REST API for programmatic control:

#### Get Subnet Information
```bash
curl http://localhost:5000/api/subnets
```

#### Toggle Subnet State
```bash
curl -X POST http://localhost:5000/api/subnet/1.2/toggle
```

#### Set Subnet Limit
```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{"limit": 50}' \
  http://localhost:5000/api/subnet/1.2/limit
```

## Configuration Files

### Tor Configuration (`tor.cfg`)
Base Tor configuration applied to all instances.

### Privoxy Configuration (`privoxy.cfg`)
HTTP proxy configuration template.

### HAProxy Configuration (`haproxy.cfg`)
Load balancer configuration for multiple proxy instances.

## Monitoring & Logs

### Container Logs
```bash
# View all logs
docker-compose logs -f

# View admin panel logs
docker-compose logs -f tor-proxy-admin | grep admin_panel
```

### HAProxy Statistics
- **URL**: http://localhost:4444/stats
- **Features**: Connection stats, server health, traffic metrics

## Security Considerations

1. **Network Isolation**: Run in isolated Docker network
2. **Access Control**: Restrict admin panel access (port 5000)
3. **Exit Node Selection**: Use geographic filtering for compliance
4. **Traffic Monitoring**: Monitor via HAProxy stats and logs

## Troubleshooting

### Admin Panel Not Loading
```bash
# Check if service is running
docker-compose ps

# Check admin panel logs
docker-compose logs tor-proxy-admin | grep admin_panel

# Restart container
docker-compose restart tor-proxy-admin
```

### Proxy Not Working
```bash
# Check Tor instances
docker-compose exec tor-proxy-admin ps aux | grep tor

# Test direct connection
curl --proxy http://localhost:3128 http://httpbin.org/ip
```

### WebSocket Connection Issues
- Check firewall settings for port 5000
- Verify WebSocket support in browser
- Check browser console for connection errors

## Development

### Local Development
```bash
# Install Python dependencies
pip install -r requirements.txt

# Run admin panel locally
python admin_panel.py

# Access at http://localhost:5000
```

### Customization
- Modify `templates/admin.html` for UI changes
- Update `admin_panel.py` for backend functionality
- Adjust Docker configuration in `Dockerfile`

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

See LICENSE file for details.

## Support

For issues and questions:
1. Check the troubleshooting section
2. Review container logs
3. Create an issue with detailed information
