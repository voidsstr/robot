#!/bin/bash
#
# Install everything needed to build the Raspberry Pi binaries and to
# compile/flash the Arduino sketch.
#
# Safe to re-run. Uses sudo for the apt + arduino-cli steps.
#
#   ./scripts/install_deps.sh
#
set -e

echo "==> Installing APT build dependencies (g++, ncurses, boost, libusb, gpsd, BlueZ, etc.)"
sudo apt-get update
sudo apt-get install -y \
    build-essential pkg-config curl git \
    libncurses-dev libncursesw5-dev \
    libboost-all-dev \
    libusb-1.0-0-dev \
    libgps-dev \
    bluez python3 python3-dbus python3-gi python3-pip

echo "==> Installing the bluezero BLE library (for scripts/ble_server.py)"
# Debian 12 marks the system Python as externally managed (PEP 668); bluezero
# isn't packaged, so install it for the system Python the BLE service uses.
sudo pip3 install --break-system-packages bluezero || \
    pip3 install --user bluezero || \
    echo "  (bluezero install failed — BLE control will be unavailable until it is installed)"

# --- arduino-cli -------------------------------------------------------------
if ! command -v arduino-cli >/dev/null 2>&1; then
    echo "==> Installing arduino-cli into /usr/local/bin"
    tmp="$(mktemp -d)"
    curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \
        | BINDIR="$tmp" sh
    sudo install -m 0755 "$tmp/arduino-cli" /usr/local/bin/arduino-cli
    rm -rf "$tmp"
else
    echo "==> arduino-cli already installed: $(arduino-cli version)"
fi

echo "==> Installing Arduino AVR core + Servo library"
arduino-cli config init --overwrite >/dev/null 2>&1 || true
arduino-cli core update-index
arduino-cli core install arduino:avr
arduino-cli lib install Servo

# --- RPLIDAR SDK static lib (needed only by the full "robot" binary) ---------
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)        RP_DIR="dependencies/lib/rplidar/x64" ;;
    aarch64|arm64) RP_DIR="dependencies/lib/rplidar/arm64" ;;
    i386|i686)     RP_DIR="dependencies/lib/rplidar/x86" ;;
    *)             RP_DIR="dependencies/lib/rplidar/x86" ;;
esac
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
if [ ! -f "$PROJECT_DIR/$RP_DIR/librplidar_sdk.a" ]; then
    echo "==> Building Slamtec RPLIDAR SDK for $ARCH ($RP_DIR)"
    tmp="$(mktemp -d)"
    git clone --depth 1 -b release/v1.9.1 https://github.com/Slamtec/rplidar_sdk.git "$tmp/rplidar_sdk"
    # Old SDK has two spots modern GCC rejects.
    sed -i 's/return ans<=0?RESULT_OPERATION_FAIL:RESULT_OK;/return ans==NULL?RESULT_OPERATION_FAIL:RESULT_OK;/' \
        "$tmp/rplidar_sdk/sdk/sdk/src/arch/linux/net_socket.cpp"
    make -C "$tmp/rplidar_sdk/sdk" CXXEXTRA="-Wno-narrowing -Wno-error" CEXTRA="-Wno-narrowing -Wno-error"
    mkdir -p "$PROJECT_DIR/$RP_DIR"
    cp "$tmp/rplidar_sdk/sdk/output/Linux/Release/librplidar_sdk.a" "$PROJECT_DIR/$RP_DIR/"
    rm -rf "$tmp"
else
    echo "==> RPLIDAR SDK lib already present: $RP_DIR/librplidar_sdk.a"
fi

echo
echo "All dependencies installed. Build with:"
echo "  make            # robot, robot_daemon, wifi_client"
echo "  make upload     # flash the Arduino sketch"
