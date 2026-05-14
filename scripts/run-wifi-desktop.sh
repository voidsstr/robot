#!/usr/bin/env bash
# Launch `bin/robot wifi-server` + the desktop GUI together.
#
# The BLE bridge (scripts/ble_server.py) and the desktop GUI fight over
# the same camera + lidar USB devices, so we make sure robot-ble.service
# is stopped before starting the GUI.  When this script exits we
# (optionally) leave robot-ble alone — restart it manually if you want
# BLE control back.
#
# Usage:
#   bash scripts/run-wifi-desktop.sh                       # auto GPS
#   bash scripts/run-wifi-desktop.sh --lat 40.7 --lng -74  # fixed pos
#   bash scripts/run-wifi-desktop.sh --no-lidar            # GUI camera only
# Any --flag is forwarded to wifi_desktop.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

WIFI_BIN="$HERE/bin/robot"
if [ ! -x "$WIFI_BIN" ]; then
  echo "$WIFI_BIN not built. Run: make" >&2
  exit 1
fi

# Stop the BLE bridge if it's running — it owns /dev/video0 + /dev/ttyUSB0.
if systemctl is-active --quiet robot-ble 2>/dev/null; then
  echo "==> stopping robot-ble.service so the GUI can grab the camera/lidar"
  sudo systemctl stop robot-ble || true
fi

# Start the GUI in the background.  Use exec'd subshell so we can track
# its PID and tear it down when wifi-server exits / Ctrl-C arrives.
echo "==> launching desktop GUI"
python3 "$HERE/scripts/wifi_desktop.py" "$@" &
GUI_PID=$!

cleanup() {
  if kill -0 "$GUI_PID" 2>/dev/null; then
    kill "$GUI_PID" 2>/dev/null || true
    # Give it half a second to shut Tk down cleanly.
    sleep 0.5
    kill -KILL "$GUI_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

# Run wifi-server in the foreground.  Pass any extra args after `--`.
echo "==> launching bin/robot wifi-server"
exec "$WIFI_BIN" wifi-server
