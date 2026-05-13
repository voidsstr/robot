#!/bin/bash
#
# Compile and flash the Arduino motor-controller sketch over USB.
#
#   ./scripts/deploy_arduino.sh                 # auto-detect board + port
#   ./scripts/deploy_arduino.sh /dev/ttyUSB0    # force a port
#   FQBN=arduino:avr:nano ./scripts/deploy_arduino.sh
#
# Requires arduino-cli + the arduino:avr core + the Servo library
# (run ./scripts/install_deps.sh first).
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SKETCH="$PROJECT_DIR/src/Arduino/robot/robot.ino"

if ! command -v arduino-cli >/dev/null 2>&1; then
    echo "arduino-cli not found — run ./scripts/install_deps.sh" >&2
    exit 1
fi

FQBN="${FQBN:-arduino:avr:uno}"
PORT="${1:-$PORT}"

# Auto-detect the port (and FQBN, if arduino-cli recognises the board).
if [ -z "$PORT" ]; then
    detected="$(arduino-cli board list 2>/dev/null | awk 'NR>1 && $1 ~ /^\/dev\// {print $1; exit}')"
    if [ -n "$detected" ]; then
        PORT="$detected"
        # The FQBN column (e.g. "arduino:avr:uno") may have spaces in the Board
        # Name to its left, so pick the field that looks like a 3-part FQBN —
        # not just $NF, which is the Core column ("arduino:avr").
        fqbn_detected="$(arduino-cli board list 2>/dev/null | awk -v p="$PORT" \
            '$1==p {for (i=1;i<=NF;i++) if ($i ~ /^arduino:[^:]+:[^:]+$/) {print $i; exit}}')"
        case "$fqbn_detected" in arduino:*:*) FQBN="$fqbn_detected" ;; esac
    fi
fi
if [ -z "$PORT" ]; then
    for cand in /dev/ttyACM0 /dev/ttyACM1 /dev/ttyUSB0 /dev/ttyUSB1; do
        [ -e "$cand" ] && PORT="$cand" && break
    done
fi
if [ -z "$PORT" ]; then
    echo "Could not find an Arduino serial port. Plug it in, or pass the port:" >&2
    echo "  ./scripts/deploy_arduino.sh /dev/ttyACM0" >&2
    exit 1
fi

echo "==> Board: $FQBN   Port: $PORT"
echo "==> Compiling $SKETCH"
arduino-cli compile --fqbn "$FQBN" "$SKETCH"
echo "==> Uploading"
arduino-cli upload --fqbn "$FQBN" -p "$PORT" "$SKETCH"
echo "Done. The Arduino now listens for U/D/L/R/S commands at 115200 baud on $PORT."
