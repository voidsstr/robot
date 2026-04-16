#!/bin/bash

# Robot Daemon Installation Script
# Run this on the Raspberry Pi after building the project

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "==================================="
echo "  Robot Daemon Installation Script"
echo "==================================="
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root (sudo)"
    exit 1
fi

# Check if binary exists
if [ ! -f "$PROJECT_DIR/bin/robot_daemon" ]; then
    echo "Error: robot_daemon binary not found!"
    echo "Please build the project first with: make daemon"
    exit 1
fi

echo "Installing robot_daemon binary..."
cp "$PROJECT_DIR/bin/robot_daemon" /usr/local/bin/
chmod +x /usr/local/bin/robot_daemon

echo "Installing systemd service..."
cp "$SCRIPT_DIR/robot-daemon.service" /etc/systemd/system/

echo "Reloading systemd..."
systemctl daemon-reload

echo "Enabling service to start on boot..."
systemctl enable robot-daemon.service

echo ""
echo "Installation complete!"
echo ""
echo "Commands:"
echo "  Start:   sudo systemctl start robot-daemon"
echo "  Stop:    sudo systemctl stop robot-daemon"
echo "  Status:  sudo systemctl status robot-daemon"
echo "  Logs:    sudo journalctl -u robot-daemon -f"
echo ""
echo "The daemon will automatically start on boot."
echo ""

read -p "Start the daemon now? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    systemctl start robot-daemon
    systemctl status robot-daemon
fi
