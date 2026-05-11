#!/bin/bash
#
# Build the Raspberry Pi binaries (robot, robot_daemon, wifi_client).
# Thin wrapper around `make`; pass extra targets/variables straight through.
#
#   ./scripts/build_pi.sh              # == make all
#   ./scripts/build_pi.sh daemon       # just the headless WiFi daemon
#   ./scripts/build_pi.sh clean all
#
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if ! command -v g++ >/dev/null 2>&1 || ! pkg-config --exists ncurses 2>/dev/null; then
    echo "Build tools / libraries missing — run ./scripts/install_deps.sh first." >&2
    exit 1
fi

cd "$PROJECT_DIR"
exec make "${@:-all}"
