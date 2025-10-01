#!/bin/bash

set -euo pipefail

# Resolve script directory (works for symlinks too)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Allow overriding the install directory from the environment; default to the script directory (keep everything in the current repo)
PROJECT_DIR="${PROJECT_DIR:-$SCRIPT_DIR}"
SERVICE_NAME="rotating-tor-http-proxy"
SERVICE_USER="${USER}"
DEFAULT_TOR_INSTANCES=50

# Using the project directory (the repository directory by default).
echo "Using project directory: $PROJECT_DIR"


VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

echo "installing package into venv"
"$VENV_DIR/bin/python" -m pip install -e "$PROJECT_DIR"

SERVICE_SRC_FILE="$PROJECT_DIR/systemd/rotating-tor-http-proxy.service"
echo "systemd unit installation is skipped by this user-mode installer. To install the unit as root later, run:"
echo "  sudo install -m 644 \"$SERVICE_SRC_FILE\" /etc/systemd/system/$(basename \"$SERVICE_SRC_FILE\")"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable --now $SERVICE_NAME"

# Final instructions
cat <<-EOF

Installation finished.

Service management:
  sudo systemctl start $SERVICE_NAME    # Start
  sudo systemctl stop $SERVICE_NAME     # Stop
  sudo systemctl restart $SERVICE_NAME  # Restart
  sudo systemctl status $SERVICE_NAME   # Status
  journalctl -u $SERVICE_NAME -f        # Logs

+
+Virtual environment:
+  Activate it to run commands manually:
+    source $VENV_DIR/bin/activate
+  Or run directly:
+    $VENV_DIR/bin/python -m your_package_module
+
+Systemd services:
+  If you run the service via systemd, update the unit's ExecStart to use the virtualenv Python, e.g.:
+    ExecStart=$VENV_DIR/bin/python -m your_package_module
+  Or create a systemd drop-in that adjusts ExecStart accordingly.
 
 EOF
