# Source Files

This directory contains all the source files and configurations for the Tor HTTP Proxy application:

## Python Scripts
- `admin_panel.py` - Flask web interface for managing the proxy
- `config_manager.py` - Configuration management for Tor, Privoxy, and HAProxy
- `haproxy_manager.py` - HAProxy specific management functions

## Configuration Files
- `tor.cfg` - Tor configuration template
- `privoxy.cfg` - Privoxy configuration template
- `haproxy.cfg` - HAProxy configuration template

## Scripts
- `start_with_admin.sh` - Main startup script

## Other
- `requirements.txt` - Python dependencies
- `templates/` - HTML templates for the web interface
