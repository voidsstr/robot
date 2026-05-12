# Robot Control System

A tank-style robot you can drive over **WiFi** (a terminal client on the LAN) or **Bluetooth LE** (from an iPhone/Android). Movement commands land on a listener on the Raspberry Pi, which forwards them to an Arduino over the USB cable; the Arduino feeds R/C servo signals to a Sabertooth dual motor driver that powers the tank treads. The WiFi and BLE paths share the same command parser and the same Arduino link — BLE runs as a thin bridge in front of the WiFi command server. There's also an optional **lawn camera** (Arducam IMX519) that snaps photos and asks the Claude API to assess turf health — see *Lawn camera + Claude vision* below.

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

A small Python service that runs on the Pi. It advertises a BLE GATT peripheral (the Nordic UART Service) so a phone can pair with it, parses each command it receives, and writes the matching single-character control byte (`U`/`D`/`L`/`R`/`S`) **directly** to the Arduino over `/dev/ttyACM0`. No TCP loopback, no WiFi server required — BLE control is fully standalone. See **Bluetooth (BLE) control** below for the UUIDs and phone setup.

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
- **Camera / Claude vision:** `python3-picamera2`, `python3-pil`, the `rpicam-apps`/`libcamera-apps` CLI, and the `anthropic` pip package (used by `scripts/lawn_camera.py`). You still need to enable the IMX519 overlay yourself — see **Lawn camera + Claude vision** below.
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

Write **one command per BLE write** to the RX characteristic (a trailing newline is optional). The accepted strings are the same set the WiFi path uses: `UP`/`FORWARD`/`W`, `DOWN`/`BACK`/`S`, `LEFT`/`A`, `RIGHT`/`D`, `STOP`/`SPACE`/`X`, `STATUS`. Subscribe to the TX characteristic to receive the bridge's `OK: …` / `ERR: …` replies as notifications.

The bridge opens `/dev/ttyACM0` itself and writes one byte per command straight to the Arduino — no WiFi server, no TCP loopback. Since both the WiFi path and the BLE path want exclusive access to the same serial port, run **either** `robot wifi-server` / `robot_daemon` **or** `ble_server.py` at a time, not both.

### Pairing from a phone

No special app is required — any BLE/NUS terminal works for testing:

- **iPhone:** [nRF Connect](https://apps.apple.com/app/nrf-connect-for-mobile/id1054362403), [Adafruit Bluefruit Connect](https://apps.apple.com/app/adafruit-bluefruit-le-connect/id830125974) (its "Controller → Control Pad" sends `!B…` strings — for that, drive it from a custom app instead, or just use nRF Connect), or [LightBlue](https://apps.apple.com/app/lightblue/id557428110).
- **Android:** [nRF Connect](https://play.google.com/store/apps/details?id=no.nordicsemi.android.mcp) or [Serial Bluetooth Terminal](https://play.google.com/store/apps/details?id=de.kai_morich.serial_bluetooth_terminal) (BLE mode).

Steps:

1. On the Pi, make sure Bluetooth is up: `sudo bluetoothctl power on`, then run the BLE bridge (or `systemctl start robot-ble`).
2. In the phone app, scan and connect to **`RobotBLE`** (change the name with `--name`). BLE "pairing" here is just connect — there's no PIN unless you add one in BlueZ.
3. Find the service `6E40…0001`, write `UP` (or `U`, `FORWARD`, …) to characteristic `6E40…0002`, write `STOP` to halt. Enable notifications on `6E40…0003` to see replies.

For a polished on-screen D-pad, write a tiny app (Flutter `flutter_blue_plus`, React-Native `react-native-ble-plx`, Swift `CoreBluetooth`, Kotlin `BluetoothGatt`) that connects to the service above and writes `UP`/`DOWN`/`LEFT`/`RIGHT` on button-down and `STOP` on button-up.

## Lawn camera + Claude vision

`scripts/lawn_camera.py` takes a photo with the Pi camera (Arducam **IMX519**) and, *if the photo contains a lawn*, sends it to the **Claude API** (vision) for a turf-health assessment. Claude first decides whether a lawn is the subject of the shot; if not, it just reports `lawn_present: false` and nothing else is graded.

### Hardware / one-time setup

The IMX519 is a libcamera sensor — it needs its device-tree overlay enabled:

1. Add `dtoverlay=imx519` to `/boot/firmware/config.txt` (on older images, `/boot/config.txt`). Arducam also publishes an installer that adds the overlay and a tuning file — follow [their IMX519 guide](https://docs.arducam.com/Raspberry-Pi-Camera/Native-camera/16MP-IMX519/) for your OS.
2. Reboot. `rpicam-hello` (or `libcamera-hello`) should now show the camera; `rpicam-still -o test.jpg` should capture a frame.
3. `scripts/install_deps.sh` installs the Python side: `python3-picamera2`, `python3-pil`, and the `anthropic` SDK. The script uses **picamera2** if available and falls back to the `rpicam-still` / `libcamera-still` CLI.

### Running it

```bash
export ANTHROPIC_API_KEY=sk-ant-...

python3 scripts/lawn_camera.py                       # capture + assess, print the report
python3 scripts/lawn_camera.py --save-dir ~/lawn     # also save lawn-<ts>.jpg + lawn-<ts>.json
python3 scripts/lawn_camera.py --image photo.jpg     # assess an existing file (no capture)
python3 scripts/lawn_camera.py --no-api              # just capture, skip the Claude call
python3 scripts/lawn_camera.py --interval 3600       # loop forever, one assessment per hour
```

To run it on a schedule, install the unit (it reads `ANTHROPIC_API_KEY` from an env file):

```bash
sudo mkdir -p /etc/robot && echo 'ANTHROPIC_API_KEY=sk-ant-...' | sudo tee /etc/robot/lawn.env
sudo cp scripts/robot-lawncam.service /etc/systemd/system/
# edit ExecStart's repo path / interval / --save-dir if needed
sudo systemctl daemon-reload && sudo systemctl enable --now robot-lawncam
```

### What you get back

The script asks Claude for a structured result (via the SDK's `messages.parse()` with a Pydantic schema, model `claude-opus-4-7`, adaptive thinking, and the turf-care rubric cached as the system prompt):

| Field | Meaning |
|-------|---------|
| `lawn_present` / `confidence` | Whether a managed lawn is the subject of the photo, and how sure Claude is |
| `health_status` | `healthy` / `fair` / `stressed` / `unhealthy` / `no_lawn` / `unknown` |
| `health_score` | 0 (dead/bare) – 100 (lush, dense, uniform, weed-free) |
| `issues` | Visible problems: brown patches, weeds, thin/bare spots, drought stress, disease, overgrown, … |
| `recommendations` | Concrete turf-care actions (water, mow at X, overseed, fertilise, spot-treat weeds, dethatch, …) |
| `summary` | One- or two-sentence plain-language verdict |

With `--save-dir`, each run also writes the JPEG and a JSON report (assessment + model, request ID, and token usage) — handy for tracking a lawn's condition over time. Costs are billed to your Anthropic key; the photo is downscaled to ~1568 px before sending to keep tokens/latency down.

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
│   ├── install_deps.sh             # apt + arduino-cli + bluezero + anthropic + RPLIDAR SDK
│   ├── build_pi.sh                 # thin wrapper around `make`
│   ├── deploy_arduino.sh           # compile + flash the sketch (auto port/board)
│   ├── ble_server.py               # BLE (Nordic UART Service) → command-server bridge
│   ├── lawn_camera.py              # IMX519 capture → Claude vision lawn-health assessment
│   ├── install_daemon.sh           # install robot-daemon.service
│   ├── robot-daemon.service        # systemd unit (WiFi command server)
│   ├── robot-ble.service           # systemd unit (BLE bridge)
│   └── robot-lawncam.service       # systemd unit (periodic lawn camera)
└── README.md
```

## Troubleshooting

**Motors don't respond.** Confirm the Arduino is on `/dev/ttyACM0` (`ls /dev/ttyACM* /dev/ttyUSB*`); if it's `ttyUSB0`, update `ARDUINO_SERIAL_PORT` in `include/NavigationCoordinator.h`. Test the link from the Pi: `echo -n 'U' > /dev/ttyACM0`. Then check the Arduino→Sabertooth side: D10→S1, D11→S2, Arduino GND→Sabertooth 0V, and the Sabertooth DIP switches set to R/C / independent. Verify the Sabertooth has battery power and its motor terminals are wired.

**`/dev/ttyACM0` missing or "Permission denied".** Add your user to the `dialout` group: `sudo usermod -a -G dialout $USER` then log out/in. If the device node never appears, try another USB cable (some are charge-only) and `dmesg | tail` right after plugging in.

**Robot lurches for a moment on startup, then settles.** Expected — opening the USB port resets the Arduino and it re-attaches the servo outputs. The 2 s wait in `NavigationCoordinator::Start()` covers the bootloader; the Sabertooth holds neutral until it sees a valid signal.

**Client can't connect.** `nc -zv <pi-ip> 8080` from the control machine. If it fails, confirm `robot wifi-server` is running and that the Pi's firewall allows 8080 (`sudo ufw allow 8080/tcp`).

**One tread runs backwards / channels swapped.** Either swap the D10/D11 wires at S1/S2, or flip that channel's direction with the Sabertooth DIP switch. If a tread creeps when it should be stopped, trim the Sabertooth's R/C center calibration (per its manual).

**Motors twitch or cut out.** Usually a power/ground issue on the Sabertooth side — make sure the Sabertooth `0V` is tied to Arduino `GND`, the motor battery can supply stall current, and the signal wires aren't routed alongside the motor leads.

**Phone won't connect / `RobotBLE` not advertising.** Confirm the controller is up (`bluetoothctl show` → `Powered: yes`; `sudo bluetoothctl power on`). Run `python3 scripts/ble_server.py` directly to see errors — missing deps → `pip3 install --break-system-packages bluezero pyserial`; "No Bluetooth adapter" → no/blocked adapter (`rfkill unblock bluetooth`). On Pi 3/4/5 the on-board BT works out of the box; on a Pi running a serial-console on the same UART, BT may be on the "mini-UART" and slower but still fine for this.

**BLE connects but the robot doesn't move.** The bridge logs `could not write to Arduino on /dev/ttyACM0` if the USB cable isn't plugged in or another process owns the port (typical culprit: `robot wifi-server` / `robot_daemon` is still running and holding `/dev/ttyACM0`). Stop the WiFi listener before starting the BLE bridge, or vice versa. Check `ls -l /dev/ttyACM0` and `lsof /dev/ttyACM0`.

**Lawn camera can't capture.** `rpicam-hello` should preview the IMX519; if it can't, the overlay isn't active — confirm `dtoverlay=imx519` is in `/boot/firmware/config.txt` and you rebooted, and check `dmesg | grep -i imx519`. `lawn_camera.py` falls back to `rpicam-still` / `libcamera-still` if `python3-picamera2` is missing; install it (or the whole `scripts/install_deps.sh`) if you see "no way to capture an image".

**`lawn_camera.py` errors on the API call.** `ANTHROPIC_API_KEY is not set` → export it (or pass `--no-api` to just capture). `ModuleNotFoundError: anthropic` → `pip3 install --break-system-packages anthropic`. Auth/permission errors come from the key itself — check it in the Anthropic Console. Use `--image somefile.jpg` to test the API path without the camera.

## Other Modes (not covered here)

The codebase also contains `robot` / `client` radio modes (CRTP over a USB dongle) and a relay-server mode. Those are independent of the WiFi path above and aren't required for local WiFi tank control.
