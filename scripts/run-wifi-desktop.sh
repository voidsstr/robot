#!/usr/bin/env bash
# Launch `bin/robot wifi-server` + the desktop GUI + the BLE bridge together.
#
# Layout in this script:
#
#   1. Source ~/.config/robot/env (Anthropic key for Lawn Check).
#   2. Stop robot-ble.service if running (we'll start our own ble_server
#      with --no-camera --no-lidar --no-serial --forward-tcp so it doesn't
#      fight wifi-server for /dev/ttyACM0 / camera / lidar).
#   3. Background: sudo python3 ble_server.py … → /tmp/robot-ble.log
#      The GUI tails that log via --ble-log.
#   4. Background: python3 wifi_desktop.py --ble-log /tmp/robot-ble.log …
#   5. Foreground: bin/robot wifi-server (owns the Arduino).
#   6. On exit / Ctrl-C, kill both background processes.
#
# Usage:
#   bash scripts/run-wifi-desktop.sh                # camera + lidar (if attached) + BLE
#   bash scripts/run-wifi-desktop.sh --no-lidar     # GUI camera + drive only
#   bash scripts/run-wifi-desktop.sh --no-ble       # skip the BLE bridge
# Any --flag we don't recognise is forwarded to wifi_desktop.py.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

WIFI_BIN="$HERE/bin/robot"
if [ ! -x "$WIFI_BIN" ]; then
  echo "$WIFI_BIN not built. Run: make" >&2
  exit 1
fi

# Pick up ANTHROPIC_API_KEY (and friends) for the Lawn Check button.
for envfile in "${XDG_CONFIG_HOME:-$HOME/.config}/robot/env" /etc/robot/lawn.env; do
  if [ -r "$envfile" ]; then
    set -a; . "$envfile"; set +a
    break
  fi
done

# Pull out --no-ble before forwarding the rest of the args to the GUI.
WANT_BLE=1
GUI_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --no-ble) WANT_BLE=0 ;;
    *)        GUI_ARGS+=("$arg") ;;
  esac
done

# Stop the BLE bridge if it's running — we'll start our own with
# different flags so it can coexist with wifi-server.
if systemctl is-active --quiet robot-ble 2>/dev/null; then
  echo "==> stopping robot-ble.service so our flagged ble_server can take over"
  sudo systemctl stop robot-ble || true
fi

BLE_LOG=/tmp/robot-ble.log
BLE_PID=""
if [ "$WANT_BLE" = "1" ]; then
  : > "$BLE_LOG" || true
  # Run as root (BlueZ peripheral needs it) in its own process group so
  # the cleanup trap can kill the whole group.  -n: don't prompt; fail
  # fast if no cached sudo cred (we've usually got one from the `stop`
  # above).
  echo "==> launching BLE bridge (sudo, output → $BLE_LOG)"
  # -u: unbuffered, so events land in the log immediately (otherwise
  # Python block-buffers ~8 KB before the GUI's tail sees anything).
  # --forward-tcp 127.0.0.1:8081 → GUI's BleRelay (wifi-server is
  #   single-client and held by the GUI's CommandClient).
  # --frame-source 127.0.0.1:8082 → GUI's JpegFramePublisher (BLE
  #   bridge subscribes to the GUI's camera so phones see video and the
  #   GUI keeps its preview without fighting picamera2).
  # --no-lidar: the GUI owns the RPLidar too.
  # --watchdog 0.5: if the phone stops sending for 500 ms we push STOP to
  # the Arduino. Phone apps that don't fire "release → STOP" events still
  # get non-sticky behaviour: the robot keeps going only while the user
  # is actively pressing a button.
  setsid sudo -n -E python3 -u "$HERE/scripts/ble_server.py" \
    --no-lidar --no-serial \
    --forward-tcp 127.0.0.1:8081 \
    --frame-source 127.0.0.1:8082 \
    --watchdog 0.5 \
    >> "$BLE_LOG" 2>&1 &
  BLE_PID=$!
  # Give sudo / dbus a moment so the GUI's tail starts on real content.
  sleep 0.4
  if ! kill -0 "$BLE_PID" 2>/dev/null; then
    echo "WARNING: BLE bridge failed to start; see $BLE_LOG" >&2
    BLE_PID=""
  fi
fi

# Launch the GUI in the background.
echo "==> launching desktop GUI"
GUI_EXTRA=()
if [ -n "${BLE_PID:-}" ]; then
  GUI_EXTRA+=(--ble-log "$BLE_LOG")
fi
python3 "$HERE/scripts/wifi_desktop.py" "${GUI_EXTRA[@]}" "${GUI_ARGS[@]}" &
GUI_PID=$!

cleanup() {
  if [ -n "${GUI_PID:-}" ] && kill -0 "$GUI_PID" 2>/dev/null; then
    kill "$GUI_PID" 2>/dev/null || true
    sleep 0.5
    kill -KILL "$GUI_PID" 2>/dev/null || true
  fi
  if [ -n "${BLE_PID:-}" ]; then
    # We started it via `setsid sudo`, so kill the whole process group as
    # root.  -- separator stops sudo from interpreting the negative PID.
    sudo -n kill -TERM -- "-$BLE_PID" 2>/dev/null || true
    sleep 0.3
    sudo -n kill -KILL -- "-$BLE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "==> launching bin/robot wifi-server"
"$WIFI_BIN" wifi-server
