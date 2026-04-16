# Robot Control System

A remotely-controlled robot platform using Raspberry Pi and Arduino, with support for WiFi control, radio communication, LIDAR mapping, and autonomous capabilities.

## Architecture Overview

The system supports multiple control modes:

1. **WiFi Control** - Control robot over TCP/IP from any device on the network
2. **Radio Control** - Direct radio communication using USB dongle (CRTP protocol)
3. **Relay Server** - Internet-scale control through a relay server

## Hardware Wiring

### GPIO Connection Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     RASPBERRY PI ←→ ARDUINO GPIO WIRING                     │
└─────────────────────────────────────────────────────────────────────────────┘

   RASPBERRY PI                                              ARDUINO UNO
   ┌─────────────────────────┐                    ┌─────────────────────────┐
   │  3.3V (Pin 1)       ○───┼────────────────────┼───○ (not connected)     │
   │  5V   (Pin 2)       ○   │                    │   ○ VIN                 │
   │  GPIO 17 (Pin 11)   ●───┼──── Accelerate ────┼───● D7                  │
   │  GPIO 18 (Pin 12)   ●───┼──── Decelerate ────┼───● D6                  │
   │  GPIO 27 (Pin 13)   ●───┼──── Rotate Right ──┼───● D5                  │
   │  GPIO 22 (Pin 15)   ●───┼──── Rotate Left ───┼───● D4                  │
   │  GPIO 23 (Pin 16)   ●───┼──── Stop ──────────┼───● D2                  │
   │  GND    (Pin 6)     ●───┼──── Ground ────────┼───● GND                 │
   │                         │                    │                         │
   └─────────────────────────┘                    │   ● D10 ──── Left Servo │
                                                  │   ● D11 ──── Right Servo│
                                                  └─────────────────────────┘
```

### Complete Wiring Table

| Function       | Raspberry Pi          | Wire Color | Arduino Pin | Notes                    |
|----------------|----------------------|------------|-------------|--------------------------|
| Accelerate     | GPIO 17 (Pin 11)     | Green      | D7          | 10ms HIGH pulse          |
| Decelerate     | GPIO 18 (Pin 12)     | Yellow     | D6          | 10ms HIGH pulse          |
| Rotate Right   | GPIO 27 (Pin 13)     | Orange     | D5          | 10ms HIGH pulse          |
| Rotate Left    | GPIO 22 (Pin 15)     | Blue       | D4          | 10ms HIGH pulse          |
| Stop           | GPIO 23 (Pin 16)     | Red        | D2          | 10ms HIGH pulse          |
| Ground         | GND (Pin 6, 9, etc.) | Black      | GND         | Common ground (required) |

### Servo Connections (Arduino)

| Servo        | Arduino Pin | Wire Color |
|--------------|-------------|------------|
| Left Motor   | D10         | Signal     |
| Right Motor  | D11         | Signal     |
| Servo Power  | External 5V | Red        |
| Servo Ground | GND         | Black      |

**Important:** Power servos from an external 5V supply, NOT from Arduino's 5V pin!

### Physical Pin Layout (Raspberry Pi)

```
                    Raspberry Pi GPIO Header
                    ┌─────────────────────┐
              3.3V  │ 1 ●           ○ 2  │  5V
           I2C SDA  │ 3 ○           ○ 4  │  5V
           I2C SCL  │ 5 ○           ○ 6  │  GND ◄─── Common Ground
                    │ 7 ○           ○ 8  │  UART TX
               GND  │ 9 ○           ○ 10 │  UART RX
    Accelerate ───▶ │ 11 ●          ○ 12 │ ◄─── Decelerate
   Rotate Right ──▶ │ 13 ●          ○ 14 │  GND
    Rotate Left ──▶ │ 15 ●          ○ 16 │ ◄─── Stop
              3.3V  │ 17 ○          ○ 18 │
                    │ 19 ○          ○ 20 │  GND
                    │ ... continued ... │
                    └─────────────────────┘

    ● = Used for robot control
    ○ = Available/unused
```

## Data Flow Diagrams

### WiFi Control Mode

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        WIFI CONTROL DATA FLOW                            │
└──────────────────────────────────────────────────────────────────────────┘

  WIFI CLIENT                   RASPBERRY PI                    ARDUINO
  (wifi_client)                 (robot wifi-server)           (robot.ino)

┌─────────────────┐          ┌─────────────────────┐        ┌─────────────┐
│   Arrow Key     │          │  WifiCommandServer  │        │   GPIO      │
│   Press (UP)    │          │    (TCP:8080)       │        │   Input     │
│        │        │          │         │           │        │     │       │
│        ▼        │  TCP/IP  │         ▼           │        │     ▼       │
│  ┌───────────┐  │ "UP\n"   │  ┌─────────────┐    │  GPIO  │ ┌─────────┐ │
│  │  Send to  │──┼─────────▶│  │   Parse     │    │  Pulse │ │digitalRead│
│  │  Server   │  │          │  │   Command   │    │ (10ms) │ │  (pin)  │ │
│  └───────────┘  │          │  └─────────────┘    │        │ └─────────┘ │
│        │        │          │         │           │        │      │      │
│        │        │          │         ▼           │        │      ▼      │
│        │        │          │  ┌─────────────┐    │        │ ┌─────────┐ │
│        │        │          │  │ Navigation  │    │        │ │Accelerate│
│        │        │          │  │ Coordinator │    │        │ │ motors  │ │
│        │        │          │  └─────────────┘    │        │ └─────────┘ │
│        │        │          │         │           │        │      │      │
│        │        │          │         ▼           │        │      ▼      │
│        ▲        │          │  ┌─────────────┐    │ HIGH   │ ┌─────────┐ │
│        │        │ "OK\n"   │  │ digitalWrite│────┼───────▶│ │ Servo   │ │
│  ┌───────────┐  │◀─────────┼──│  (GPIO 17)  │    │ pulse  │ │ write() │ │
│  │  Display  │  │          │  └─────────────┘    │        │ └─────────┘ │
│  │  Response │  │          │                     │        │      │      │
│  └───────────┘  │          │                     │        │      ▼      │
└─────────────────┘          └─────────────────────┘        │ ┌─────────┐ │
                                                             │ │ MOTORS │ │
                                                             │ │  MOVE! │ │
                                                             │ └─────────┘ │
                                                             └─────────────┘
```

### GPIO Signal Protocol

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    RASPBERRY PI → ARDUINO GPIO SIGNALS                   │
└──────────────────────────────────────────────────────────────────────────┘

  RASPBERRY PI                                    ARDUINO
  (digitalWrite)                                 (digitalRead)

       │                                              │
       │  ════════ GPIO 17 HIGH (10ms) ═══════════▶  │  Accelerate
       │  ════════ GPIO 17 LOW  ═══════════════════▶ │  (leftMotor -= 3)
       │                                              │  (rightMotor -= 3)
       │                                              │
       │  ════════ GPIO 18 HIGH (10ms) ═══════════▶  │  Decelerate
       │  ════════ GPIO 18 LOW  ═══════════════════▶ │  (leftMotor += 3)
       │                                              │  (rightMotor += 3)
       │                                              │
       │  ════════ GPIO 27 HIGH (10ms) ═══════════▶  │  Rotate Right
       │  ════════ GPIO 27 LOW  ═══════════════════▶ │  (leftMotor -= 3)
       │                                              │  (rightMotor += 3)
       │                                              │
       │  ════════ GPIO 22 HIGH (10ms) ═══════════▶  │  Rotate Left
       │  ════════ GPIO 22 LOW  ═══════════════════▶ │  (rightMotor -= 3)
       │                                              │  (leftMotor += 3)
       │                                              │
       │  ════════ GPIO 23 HIGH (10ms) ═══════════▶  │  Stop
       │  ════════ GPIO 23 LOW  ═══════════════════▶ │  (both = 90)
       │                                              │

  Signal: 3.3V HIGH, 0V LOW
  Pulse Duration: 10 milliseconds
```

### Motor Control Values

```
  Servo Value    Motor State
  ───────────────────────────
      0          Full Forward
     ...              ↑
     87          Slow Forward
     90          STOPPED
     93          Slow Backward
    ...               ↓
    180          Full Backward
```

## Building

### Dependencies

```bash
# Core dependencies
sudo apt-get install libncurses-dev libboost-all-dev

# WiringPi (for GPIO access)
git clone https://github.com/WiringPi/WiringPi.git
cd WiringPi
./build
```

### Compile

```bash
# On Raspberry Pi (ARM)
g++ -o robot main.cpp src/*.cpp -I include -lncurses -lwiringPi -lpthread -std=c++14

# Compile WiFi client (on control machine - no wiringPi needed)
g++ -o wifi_client src/wifi_client.cpp -lncurses -lpthread -std=c++14
```

## Usage

### Mode 1: WiFi Control (Recommended)

**On Raspberry Pi (Robot):**
```bash
./robot wifi-server -p 8080
```

**On Control Machine:**
```bash
./wifi_client <raspberry-pi-ip> 8080
```

Client controls:
- **Arrow Keys / WASD**: Move robot
- **Space / X**: Emergency stop
- **?**: Query robot status
- **Q**: Quit

### Mode 2: Direct Radio Control

```bash
# On robot
./robot robot

# On client with radio dongle
./robot client
```

### Mode 3: Running as a System Service

```bash
# Install the daemon
sudo ./scripts/install_daemon.sh

# Control the service
sudo systemctl start robot-daemon
sudo systemctl stop robot-daemon
sudo systemctl status robot-daemon

# View logs
sudo journalctl -u robot-daemon -f
```

## File Structure

```
robot/
├── include/
│   ├── NavigationCoordinator.h   # GPIO motor control logic
│   ├── WifiCommandServer.h       # TCP server for WiFi control
│   ├── InputProcessor.h          # Keyboard input handling
│   ├── LidarManager.h            # RPLIDAR integration
│   ├── HUDManager.h              # Terminal UI
│   └── ...
├── src/
│   ├── NavigationCoordinator.cpp
│   ├── WifiCommandServer.cpp
│   ├── wifi_client.cpp           # ASCII control client
│   └── Arduino/
│       └── robot/
│           └── robot.ino         # Arduino motor controller
├── scripts/
│   ├── robot-daemon.service      # systemd service file
│   └── install_daemon.sh         # Installation script
├── main.cpp
└── README.md
```

## Arduino Setup

1. Upload `src/Arduino/robot/robot.ino` to Arduino via USB (temporarily)
2. Connect GPIO wires as shown in wiring diagram above
3. Connect servos to Arduino pins D10 and D11
4. Power servos from external 5V supply

## Troubleshooting

### GPIO not working
```bash
# Check if wiringPi is installed
gpio -v

# Test GPIO output manually
gpio mode 0 out
gpio write 0 1  # Set high
gpio write 0 0  # Set low
```

### Permission denied on GPIO
```bash
# Run as root or add user to gpio group
sudo usermod -a -G gpio $USER
# Log out and back in
```

### WiFi client can't connect
```bash
# Check if server is running
sudo systemctl status robot-daemon

# Check firewall
sudo ufw allow 8080/tcp

# Test with netcat
nc -zv 192.168.1.100 8080
```

## Protocol Reference

### WiFi Commands (TCP)

| Command | Description |
|---------|-------------|
| UP | Accelerate forward |
| DOWN | Decelerate / reverse |
| LEFT | Rotate left |
| RIGHT | Rotate right |
| STOP | Emergency stop |
| STATUS | Query connection status |
| QUIT | Disconnect |

### GPIO Signals

| GPIO (BCM) | WiringPi | Arduino | Function     |
|------------|----------|---------|--------------|
| GPIO 17    | 0        | D7      | Accelerate   |
| GPIO 18    | 1        | D6      | Decelerate   |
| GPIO 27    | 2        | D5      | Rotate Right |
| GPIO 22    | 3        | D4      | Rotate Left  |
| GPIO 23    | 4        | D2      | Stop         |

## License

MIT License
