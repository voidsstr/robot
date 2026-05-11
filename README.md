# Robot Control System

A tank-style robot you can drive over **WiFi** (a terminal client on the LAN) or **Bluetooth LE** (from an iPhone/Android). Movement commands land on a listener on the Raspberry Pi, which forwards them to an Arduino over the USB cable; the Arduino feeds R/C servo signals to a Sabertooth dual motor driver that powers the tank treads. The WiFi and BLE paths share the same command parser and the same Arduino link — BLE runs as a thin bridge in front of the WiFi command server.

Total wiring between all boards: **1 USB cable Pi↔Arduino, 2 signal wires + ground Arduino→Sabertooth; motor/battery wiring lives on the Sabertooth's own terminals.**

## System Architecture

```
  ┌────────────────────┐
  │  CONTROL COMPUTER  │  wifi_client ──┐ WiFi/TCP :8080
  │   (terminal UI)    │                │
  └────────────────────┘                │
                                        ▼
  ┌────────────────────┐         ┌──────────────────────┐         ┌──────────────────┐
  │   PHONE (iOS /     │  BLE    │    RASPBERRY PI      │         │     ARDUINO      │
  │    Android)        │◄───────►│ ble_server.py ──┐    │         │                  │
  │  nRF Connect /     │  GATT   │  WifiCommandServer ◄─┘   USB   │   robot.ino      │
  │  Bluefruit Connect │  (NUS)  │    (TCP :8080)       │◄───────►│  (R/C signal gen)│
  │                    │         │         │            │ 115200  │        │         │
  │  on-screen pad     │         │         ▼            │  1 char │        ▼         │
  │                    │         │ NavigationCoordinator│   cmds  │  D10 → S1        │
  │                    │         │  (/dev/ttyACM0)      │         │  D11 → S2        │
  └────────────────────┘         └──────────────────────┘         └────────┬─────────┘
                                                                           │ R/C servo pulses
                                                                           ▼
                                                                   ┌──────────────────┐
                                                                   │ SABERTOOTH 2x..  │
                                                                   │  (motor driver)  │
                                                                   └────────┬─────────┘
                                                                            ▼
                                                                    ┌───────────────┐
                                                                    │ TANK TREADS   │
                                                                    └───────────────┘
```

Components, each with one job:

### 1. Arduino motor controller — `src/Arduino/robot/robot.ino`

Runs continuously on the Arduino. Reads single-character commands from USB serial (`Serial`) at 115200 baud. For each command, steps two servo-pulse outputs on D10 and D11, which feed the Sabertooth's R/C inputs S1 and S2 (one channel per tread).

- Servo value `90` (≈1500 µs) = stopped
- Values `< 90` = forward (down to `0` ≈ 1000 µs = full forward)
- Values `> 90` = reverse (up to `180` ≈ 2000 µs = full reverse)
- Each `U`/`D`/`L`/`R` command steps motor values by `±3`
- `S` immediately snaps both channels back to `90` (Sabertooth neutral)

### 2. Raspberry Pi listener — `robot wifi-server`

Entry point `wifiServerLoop()` in `main.cpp`. Two pieces:

- **`WifiCommandServer`** (`src/WifiCommandServer.cpp`): listens on TCP port `8080`, accepts one client at a time, parses newline-terminated ASCII commands (`UP`, `DOWN`, `LEFT`, `RIGHT`, `STOP`, `STATUS`, `QUIT`), maps them to a `DIRECTION` enum.
- **`NavigationCoordinator`** (`src/NavigationCoordinator.cpp`): opens `/dev/ttyACM0` (the Arduino's USB serial port) at 115200 baud, waits ~2 s for the board to finish its reset, then writes one byte (`U`/`D`/`L`/`R`/`S`) per command.

Local keyboard input on the Pi is also accepted as a backup control path.

### 3. Terminal client — `src/wifi_client.cpp` → `wifi_client` binary

Runs on any computer on the same WiFi network. Opens a TCP socket to the Pi's IP on port 8080. Reads arrow keys / WASD through ncurses and sends one-line commands:

| Key             | Command sent | Effect                     |
|-----------------|--------------|----------------------------|
| ↑ / W           | `UP`         | Accelerate forward         |
| ↓ / S           | `DOWN`       | Decelerate / reverse       |
| ← / A           | `LEFT`       | Rotate left                |
| → / D           | `RIGHT`      | Rotate right               |
| Space / X       | `STOP`       | Both motors to neutral     |
| ?               | `STATUS`     | Query Pi → Arduino link    |
| Q               | —            | Quit client                |

Client shows an ASCII robot, current speed, last command, and the server log in a split ncurses UI.

### 4. Bluetooth (BLE) bridge — `scripts/ble_server.py`

A small Python service that runs *alongside* the WiFi listener on the Pi. It advertises a BLE GATT peripheral (the Nordic UART Service) so a phone can pair with it, and forwards every command it receives to `127.0.0.1:8080` — i.e. straight into `WifiCommandServer`. Nothing about the motor path changes; BLE is just another front door to the same command server. See **Bluetooth (BLE) control** below for the UUIDs and phone setup.

## Command Path — End to End

```
  User presses ↑ on laptop
          │
          ▼
  wifi_client → sends "UP\n" over TCP
          │
          ▼
  Pi: WifiCommandServer::handleClient recv()s "UP"
          │
          ▼
  parseCommand("UP") → DIRECTION::UP
          │
          ▼
  NavigationCoordinator::UpdateNavigationParameters(UP)
  NavigationCoordinator::ProcessUpdate() → Accelerate()
          │
          ▼
  SendCommand('U'):
    write(/dev/ttyACM0, "U", 1)       // single byte over USB serial @ 115200
          │
          ▼
  Arduino loop(): Serial.read() returns 'U'
          │
          ▼
  accelerate(): leftMotorLevel -= 3; rightMotorLevel -= 3;
          │
          ▼
  leftMotor.write(87); rightMotor.write(87);    // D10 → S1, D11 → S2 (≈1450 µs)
          │
          ▼
  Sabertooth drives both treads forward → TANK MOVES FORWARD
```

## Hardware Wiring

### Raspberry Pi ↔ Arduino (USB)

One USB cable from a Pi USB port to the Arduino. That's it — it carries 5 V power, programming, and the command link. No GPIO pins, no level shifting, no `raspi-config`.

The Arduino shows up on the Pi as:

| Board                          | Device node       |
|--------------------------------|-------------------|
| Uno / Mega / Leonardo / Micro  | `/dev/ttyACM0`    |
| Nano / clones with CH340/CP210x| `/dev/ttyUSB0`    |

`NavigationCoordinator` is hard-coded to `/dev/ttyACM0` (see `include/NavigationCoordinator.h`); change `ARDUINO_SERIAL_PORT` there if your board uses `ttyUSB0`. If more than one USB-serial device is present, use a stable path like `/dev/serial/by-id/...`.

**Note — the board resets when the port opens.** Opening `/dev/ttyACM0` toggles DTR, which reboots most Arduinos. `NavigationCoordinator::Start()` waits ~2 s after opening for the bootloader to hand off before sending commands; don't be surprised by a brief pause on startup.

### Arduino → Sabertooth motor driver (R/C mode, 2 signal wires + ground)

The Arduino generates two standard R/C servo signals; the Sabertooth interprets them as throttle for each channel.

| Arduino Pin | Sabertooth Pin | Carries                                  |
|-------------|----------------|------------------------------------------|
| D10         | S1             | Left-tread throttle (servo pulse)        |
| D11         | S2             | Right-tread throttle (servo pulse)       |
| GND         | 0V             | Signal ground (the pin next to S1/S2)    |

Sabertooth setup:

- Set the DIP switches for **R/C input mode**, **independent** channels (no mixing), no exponential — see the label on the unit / the Sabertooth manual for your model (2x12, 2x25, 2x32, …).
- Battery `+`/`-` and the two motors connect to the Sabertooth's own screw terminals — **not** to the Arduino. Don't try to power motors from the Arduino.
- Tie the Sabertooth `0V` to Arduino `GND` so the signals share a reference. The Pi shares ground with the Arduino through the USB cable.
- A pulse near 1500 µs is neutral; the Sabertooth will brake/stop the motors if the signal disappears, which is why `'S'` simply parks both outputs at `90`.

### Protocol (single-char commands, 115200 8N1)

| Byte | Meaning          | Effect on motors                              |
|------|------------------|-----------------------------------------------|
| `U`  | Accelerate       | Both channels step toward forward (−3 each)   |
| `D`  | Decelerate       | Both channels step toward reverse (+3 each)   |
| `L`  | Rotate Left      | Left +3, Right −3                             |
| `R`  | Rotate Right     | Left −3, Right +3                             |
| `S`  | Stop             | Both channels → 90 (Sabertooth neutral)       |

All other bytes are ignored by the Arduino. You can test the link from the Pi directly (`ttyACM0` or `ttyUSB0` to match your board):

```bash
stty -F /dev/ttyACM0 115200 raw    # configure the port once
echo -n 'U' > /dev/ttyACM0         # one forward step
echo -n 'S' > /dev/ttyACM0         # stop
```

## Building

### Install dependencies (Raspberry Pi)

```bash
make deps          # == ./scripts/install_deps.sh
```

That script installs everything in one go:

- **Build tools / libraries:** `build-essential`, `libncurses-dev`, `libboost-all-dev`, `libusb-1.0-0-dev`, `libgps-dev` — the `robot` binary's radio/LIDAR modes need libusb + Boost.Asio; the WiFi/BLE motor path needs only ncurses.
- **Arduino:** `arduino-cli` plus the `arduino:avr` core and the `Servo` library.
- **BLE:** `bluez`, `python3-dbus`, `python3-gi`, and the `bluezero` pip package (used by `scripts/ble_server.py`).
- **RPLIDAR SDK:** a static lib is vendored for x86/x64/arm64 under `dependencies/lib/rplidar/`; the script rebuilds it from source (Slamtec SDK v1.9.1) if your architecture is missing.

No WiringPi or other GPIO library is required — the Pi talks to the Arduino over its USB serial port (`/dev/ttyACM0`) using the standard `termios` API.

### Compile

```bash
make               # builds robot, robot_daemon and wifi_client into ./bin/
make robot         # full binary: radio + LIDAR + wifi-server modes
make daemon        # robot_daemon — headless WiFi command server
make client        # wifi_client — terminal control client
make arduino       # compile-check the Arduino sketch
make clean
```

`make` figures out which vendored RPLIDAR static lib to link from `uname -m`. The control-computer client needs no ARM/GPIO headers, so `make client` (or `./scripts/build_pi.sh client`) also builds on x86 laptops, Macs with ncurses, etc.

## Running

### 1. Flash the Arduino

```bash
make upload                       # arduino:avr:uno on /dev/ttyACM0
make upload FQBN=arduino:avr:nano PORT=/dev/ttyUSB0
# or, with auto port/board detection:
./scripts/deploy_arduino.sh
```

Leave the USB cable connected to the Pi afterwards — it's now the command link as well as the Arduino's power source. (Power the Sabertooth and the motors from their own battery.)

### 2. Start the listener on the Pi

```bash
./bin/robot wifi-server -p 8080
```

Or install it as a systemd service:

```bash
sudo ./scripts/install_daemon.sh
sudo systemctl start robot-daemon
sudo journalctl -u robot-daemon -f
```

### 3a. Drive over WiFi from the control computer

```bash
./bin/wifi_client <pi-ip-address> 8080      # e.g. ./bin/wifi_client 192.168.1.42 8080
```

Once connected you'll see `ROBOT READY` and the ncurses UI. Use arrow keys / WASD to drive.

### 3b. Drive over Bluetooth from a phone

Start the BLE bridge on the Pi (with the WiFi listener already running):

```bash
sudo python3 scripts/ble_server.py            # advertises as "RobotBLE"
```

or install it as a service (it starts after `robot-daemon`):

```bash
sudo cp scripts/robot-ble.service /etc/systemd/system/
# edit ExecStart's path if the repo isn't at /opt/robot
sudo systemctl daemon-reload && sudo systemctl enable --now robot-ble
```

Then pair from the phone — see the next section.

## Wire Protocol (TCP)

ASCII, newline-terminated. Case-insensitive.

| Request  | Aliases                           | Response              |
|----------|-----------------------------------|-----------------------|
| `UP`     | `FORWARD`, `W`, `ACC`, `ACCELERATE` | `OK: UP`            |
| `DOWN`   | `BACK`, `S`, `DEC`, `DECELERATE`  | `OK: DOWN`            |
| `LEFT`   | `A`                               | `OK: LEFT`            |
| `RIGHT`  | `D`                               | `OK: RIGHT`           |
| `STOP`   | `SPACE`, `X`                      | `OK: STOP`            |
| `STATUS` |                                   | `OK: Connected ...`   |
| `QUIT`   | `EXIT`                            | `BYE`                 |
| *other*  |                                   | `ERR: Unknown command`|

Any TCP client works — `netcat` is handy for debugging:

```bash
nc 192.168.1.42 8080
UP
STOP
QUIT
```

## Bluetooth (BLE) control

`scripts/ble_server.py` exposes a BLE GATT peripheral implementing the **Nordic UART Service (NUS)** — a simple two-characteristic "serial over BLE" profile that lots of phone apps already speak:

| GATT object | UUID                                   | Properties      | Direction       |
|-------------|----------------------------------------|-----------------|-----------------|
| Service     | `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` | —               | —               |
| RX          | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` | Write / WriteNR | phone → robot   |
| TX          | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` | Notify          | robot → phone   |

Write **one command per BLE write** to the RX characteristic (a trailing newline is optional). The accepted strings are exactly the TCP commands and aliases: `UP`/`FORWARD`/`W`, `DOWN`/`BACK`/`S`, `LEFT`/`A`, `RIGHT`/`D`, `STOP`/`SPACE`/`X`, `STATUS`. Subscribe to the TX characteristic to receive the server's `OK: …` / `ERR: …` replies as notifications.

The bridge just relays bytes to `127.0.0.1:8080`, so the WiFi listener (`robot wifi-server` or `robot_daemon`) must be running. Note `WifiCommandServer` handles **one TCP client at a time**, and the running BLE bridge holds that slot — so connect *either* the BLE bridge *or* a WiFi `wifi_client`, not both at once (stop one before using the other).

### Pairing from a phone

No special app is required — any BLE/NUS terminal works for testing:

- **iPhone:** [nRF Connect](https://apps.apple.com/app/nrf-connect-for-mobile/id1054362403), [Adafruit Bluefruit Connect](https://apps.apple.com/app/adafruit-bluefruit-le-connect/id830125974) (its "Controller → Control Pad" sends `!B…` strings — for that, drive it from a custom app instead, or just use nRF Connect), or [LightBlue](https://apps.apple.com/app/lightblue/id557428110).
- **Android:** [nRF Connect](https://play.google.com/store/apps/details?id=no.nordicsemi.android.mcp) or [Serial Bluetooth Terminal](https://play.google.com/store/apps/details?id=de.kai_morich.serial_bluetooth_terminal) (BLE mode).

Steps:

1. On the Pi, make sure Bluetooth is up: `sudo bluetoothctl power on`, then run the BLE bridge (or `systemctl start robot-ble`).
2. In the phone app, scan and connect to **`RobotBLE`** (change the name with `--name`). BLE "pairing" here is just connect — there's no PIN unless you add one in BlueZ.
3. Find the service `6E40…0001`, write `UP` (or `U`, `FORWARD`, …) to characteristic `6E40…0002`, write `STOP` to halt. Enable notifications on `6E40…0003` to see replies.

For a polished on-screen D-pad, write a tiny app (Flutter `flutter_blue_plus`, React-Native `react-native-ble-plx`, Swift `CoreBluetooth`, Kotlin `BluetoothGatt`) that connects to the service above and writes `UP`/`DOWN`/`LEFT`/`RIGHT` on button-down and `STOP` on button-up.

## File Layout

```
robot/
├── Makefile                        # build robot / robot_daemon / wifi_client, flash Arduino
├── main.cpp                        # entry point: robot / client / wifi-server modes
├── include/
│   ├── NavigationCoordinator.h     # USB serial-port config + command map
│   ├── WifiCommandServer.h         # TCP server declaration
│   ├── InputProcessor.h            # keyboard → DIRECTION enum
│   └── ...                         # other modes (radio, LIDAR)
├── src/
│   ├── NavigationCoordinator.cpp   # termios USB-serial send logic
│   ├── WifiCommandServer.cpp       # accept() loop, command parser
│   ├── ArduinoSerialManager.cpp    # alt serial wrapper used by robot_daemon
│   ├── robot_daemon.cpp            # headless WiFi command server (no curses UI)
│   ├── wifi_client.cpp             # terminal client (separate binary)
│   └── Arduino/
│       └── robot/robot.ino         # Arduino firmware (USB-serial → Sabertooth R/C)
├── dependencies/                   # vendored RPLIDAR SDK headers + per-arch static libs
├── scripts/
│   ├── install_deps.sh             # apt + arduino-cli + bluezero + RPLIDAR SDK
│   ├── build_pi.sh                 # thin wrapper around `make`
│   ├── deploy_arduino.sh           # compile + flash the sketch (auto port/board)
│   ├── ble_server.py               # BLE (Nordic UART Service) → command-server bridge
│   ├── install_daemon.sh           # install robot-daemon.service
│   ├── robot-daemon.service        # systemd unit (WiFi command server)
│   └── robot-ble.service           # systemd unit (BLE bridge)
└── README.md
```

## Troubleshooting

**Motors don't respond.** Confirm the Arduino is on `/dev/ttyACM0` (`ls /dev/ttyACM* /dev/ttyUSB*`); if it's `ttyUSB0`, update `ARDUINO_SERIAL_PORT` in `include/NavigationCoordinator.h`. Test the link from the Pi: `echo -n 'U' > /dev/ttyACM0`. Then check the Arduino→Sabertooth side: D10→S1, D11→S2, Arduino GND→Sabertooth 0V, and the Sabertooth DIP switches set to R/C / independent. Verify the Sabertooth has battery power and its motor terminals are wired.

**`/dev/ttyACM0` missing or "Permission denied".** Add your user to the `dialout` group: `sudo usermod -a -G dialout $USER` then log out/in. If the device node never appears, try another USB cable (some are charge-only) and `dmesg | tail` right after plugging in.

**Robot lurches for a moment on startup, then settles.** Expected — opening the USB port resets the Arduino and it re-attaches the servo outputs. The 2 s wait in `NavigationCoordinator::Start()` covers the bootloader; the Sabertooth holds neutral until it sees a valid signal.

**Client can't connect.** `nc -zv <pi-ip> 8080` from the control machine. If it fails, confirm `robot wifi-server` is running and that the Pi's firewall allows 8080 (`sudo ufw allow 8080/tcp`).

**One tread runs backwards / channels swapped.** Either swap the D10/D11 wires at S1/S2, or flip that channel's direction with the Sabertooth DIP switch. If a tread creeps when it should be stopped, trim the Sabertooth's R/C center calibration (per its manual).

**Motors twitch or cut out.** Usually a power/ground issue on the Sabertooth side — make sure the Sabertooth `0V` is tied to Arduino `GND`, the motor battery can supply stall current, and the signal wires aren't routed alongside the motor leads.

**Phone won't connect / `RobotBLE` not advertising.** Confirm the controller is up (`bluetoothctl show` → `Powered: yes`; `sudo bluetoothctl power on`). Run `python3 scripts/ble_server.py` directly to see errors — `bluezero` not installed → `pip3 install --break-system-packages bluezero`; "No Bluetooth adapter" → no/blocked adapter (`rfkill unblock bluetooth`). On Pi 3/4/5 the on-board BT works out of the box; on a Pi running a serial-console on the same UART, BT may be on the "mini-UART" and slower but still fine for this.

**BLE connects but the robot doesn't move.** The bridge logs `could not reach command server` if `robot wifi-server` / `robot_daemon` isn't running on port 8080 — start it first. Check the WiFi path works (`nc 127.0.0.1 8080` → `UP`) before blaming BLE.

## Other Modes (not covered here)

The codebase also contains `robot` / `client` radio modes (CRTP over a USB dongle) and a relay-server mode. Those are independent of the WiFi path above and aren't required for local WiFi tank control.
