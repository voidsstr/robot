#!/usr/bin/env python3
"""BLE -> Arduino command bridge.

Exposes a BLE GATT peripheral (Nordic UART Service) that an iPhone or Android
phone can pair with, parses each incoming text command, and writes the
matching single-character control byte directly to the Arduino over USB
serial (/dev/ttyACM0 @ 115200 8N1). No WiFi server, no TCP loopback, no
second daemon — this script is the only thing that needs to be running for
BLE control.

GATT layout (Nordic UART Service — supported out of the box by nRF Connect,
Adafruit Bluefruit Connect, LightBlue, etc.):

  Service  6E400001-B5A3-F393-E0A9-E50E24DCCA9E
    RX  6E400002-B5A3-F393-E0A9-E50E24DCCA9E   Write             phone -> robot
    TX  6E400003-B5A3-F393-E0A9-E50E24DCCA9E   Notify            robot -> phone

Write one command per BLE write (newline optional). Accepted commands:
  UP / FORWARD / W / ACC / ACCELERATE       -> 'U'
  DOWN / BACK / S / DEC / DECELERATE        -> 'D'
  LEFT / A                                  -> 'L'
  RIGHT / D                                 -> 'R'
  STOP / SPACE / X                          -> 'S'
  STATUS                                    -> reports serial port state

A short reply (e.g. `OK: UP`) is pushed back as a TX notification.

Requirements:  BlueZ >= 5.50, python3-dbus, python3-gi, `bluezero`, and
  `pyserial` (`pip3 install --break-system-packages bluezero pyserial`, or
  run scripts/install_deps.sh).

Run as root (BlueZ D-Bus + advertising usually needs it):
  sudo python3 scripts/ble_server.py
  sudo python3 scripts/ble_server.py --name MyRobot --serial /dev/ttyACM0
"""

import argparse
import sys
import threading
import time

NUS_SERVICE = '6E400001-B5A3-F393-E0A9-E50E24DCCA9E'
NUS_RX_CHAR = '6E400002-B5A3-F393-E0A9-E50E24DCCA9E'   # Write   (phone -> robot)
NUS_TX_CHAR = '6E400003-B5A3-F393-E0A9-E50E24DCCA9E'   # Notify  (robot -> phone)

# Path our auto-accept BlueZ agent registers under.  Any unused path works;
# it just needs to be unique and stable for the lifetime of the script.
AGENT_PATH = '/com/voidsstr/robot/agent'
BLUEZ_AGENT_IFACE = 'org.bluez.Agent1'

DEFAULT_NAME = 'RobotBLE'
DEFAULT_SERIAL_PORT = '/dev/ttyACM0'
DEFAULT_SERIAL_BAUD = 115200

# Text command -> single byte expected by src/Arduino/robot/robot.ino.
# Aliases follow the same convention WifiCommandServer::parseCommand used:
# the single-letter WASD aliases mean directions ('S' = DOWN, 'D' = RIGHT),
# NOT the Arduino's own protocol letters.
COMMAND_MAP = {
    'UP': 'U', 'FORWARD': 'U', 'W': 'U', 'ACC': 'U', 'ACCELERATE': 'U',
    'DOWN': 'D', 'BACK': 'D', 'S': 'D', 'DEC': 'D', 'DECELERATE': 'D',
    'LEFT': 'L', 'A': 'L',
    'RIGHT': 'R', 'D': 'R',
    'STOP': 'S', 'SPACE': 'S', 'X': 'S',
}


def register_auto_accept_agent():
    """Register a 'Just Works' BlueZ pairing agent on the system bus.

    By default BlueZ uses the user-interactive bluetoothctl agent — when a
    phone tries to pair, the Pi prompts the user to confirm.  We don't have
    a user at the Pi (it's headless) and we want pairing to be seamless
    from the phone's perspective.

    Registering an agent with capability 'NoInputNoOutput' tells BlueZ to
    use the 'Just Works' SSP pairing flow (no PIN, no confirmation) for
    any device that connects to us.  All the Agent1 methods below
    auto-accept whatever BlueZ asks.

    Idempotent — if a previous run left an agent registered at this path,
    we unregister it first.  Returns the agent object (caller must keep a
    reference, otherwise dbus-python garbage-collects it).
    """
    import dbus
    import dbus.service
    import dbus.mainloop.glib

    # Plug D-Bus into the GLib mainloop bluezero will start in publish().
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

    bus = dbus.SystemBus()

    class _AutoAcceptAgent(dbus.service.Object):
        # Every callback below returns "accept" / "no input needed", which is
        # exactly what NoInputNoOutput SSP requires.  See BlueZ doc/agent-api.txt
        # for the contract.
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='', out_signature='')
        def Release(self): pass
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='os', out_signature='')
        def AuthorizeService(self, device, uuid): pass
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='o', out_signature='s')
        def RequestPinCode(self, device): return '0000'
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='o', out_signature='u')
        def RequestPasskey(self, device): return dbus.UInt32(0)
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='ouq', out_signature='')
        def DisplayPasskey(self, device, passkey, entered): pass
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='os', out_signature='')
        def DisplayPinCode(self, device, pincode): pass
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='ou', out_signature='')
        def RequestConfirmation(self, device, passkey): pass
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='o', out_signature='')
        def RequestAuthorization(self, device): pass
        @dbus.service.method(BLUEZ_AGENT_IFACE, in_signature='', out_signature='')
        def Cancel(self): pass

    agent = _AutoAcceptAgent(bus, AGENT_PATH)

    manager = dbus.Interface(
        bus.get_object('org.bluez', '/org/bluez'),
        'org.bluez.AgentManager1')

    # Clear any leftover registration from a previous crashed run.
    try:
        manager.UnregisterAgent(AGENT_PATH)
    except dbus.exceptions.DBusException:
        pass

    manager.RegisterAgent(AGENT_PATH, 'NoInputNoOutput')
    manager.RequestDefaultAgent(AGENT_PATH)
    print('Auto-accept pairing agent registered (NoInputNoOutput / Just Works).')
    return agent


class ArduinoLink:
    """Auto-reconnecting USB-serial link to the Arduino motor controller."""

    def __init__(self, port, baud):
        self._port = port
        self._baud = baud
        self._ser = None
        self._lock = threading.Lock()

    def _open_locked(self):
        import serial  # imported lazily so --help works without pyserial
        s = serial.Serial(self._port, self._baud, timeout=0, write_timeout=1)
        # Opening the port toggles DTR and resets most Arduinos; wait for the
        # bootloader to hand off to the sketch before sending commands.
        time.sleep(2.0)
        try:
            s.reset_input_buffer()
        except Exception:
            pass
        self._ser = s

    def send_byte(self, ch):
        """Send a single ASCII byte to the Arduino. Returns True on success."""
        payload = ch.encode('ascii')
        with self._lock:
            for _ in range(2):
                try:
                    if self._ser is None:
                        self._open_locked()
                    self._ser.write(payload)
                    self._ser.flush()
                    return True
                except Exception as e:
                    # Drop the handle and let the next attempt reopen.
                    try:
                        if self._ser is not None:
                            self._ser.close()
                    except Exception:
                        pass
                    self._ser = None
                    last_err = e
                    time.sleep(0.2)
        print(f'[ble] could not write to Arduino on {self._port}: {last_err}', file=sys.stderr)
        return False

    def is_open(self):
        with self._lock:
            return self._ser is not None


def main():
    parser = argparse.ArgumentParser(description='BLE -> Arduino command bridge')
    parser.add_argument('--name', default=DEFAULT_NAME, help='BLE advertised name')
    parser.add_argument('--serial', default=DEFAULT_SERIAL_PORT, help='Arduino serial port')
    parser.add_argument('--baud', type=int, default=DEFAULT_SERIAL_BAUD, help='Arduino serial baud')
    parser.add_argument('--adapter', default=None, help='Bluetooth adapter address (default: first available)')
    parser.add_argument('--no-auto-accept', action='store_true',
                        help="don't register a Just-Works pairing agent — leave the system "
                             "default agent in charge.  Use this only if you've configured "
                             "your own agent (e.g. bt-agent) elsewhere; without one the Pi "
                             "will prompt for confirmation on every new phone.")
    args = parser.parse_args()

    try:
        from bluezero import adapter, peripheral
    except ImportError:
        sys.exit('bluezero is not installed. Run: pip3 install --break-system-packages bluezero\n'
                 '(also needs python3-dbus and python3-gi; see scripts/install_deps.sh)')

    try:
        import serial  # noqa: F401  -- imported here for a friendly error message
    except ImportError:
        sys.exit('pyserial is not installed. Run: pip3 install --break-system-packages pyserial')

    adapter_addr = args.adapter
    if adapter_addr is None:
        adapters = list(adapter.Adapter.available())
        if not adapters:
            sys.exit('No Bluetooth adapter found. Try: sudo bluetoothctl power on')
        adapter_addr = adapters[0].address

    link = ArduinoLink(args.serial, args.baud)

    robot = peripheral.Peripheral(adapter_addr, local_name=args.name)
    robot.add_service(srv_id=1, uuid=NUS_SERVICE, primary=True)

    # Forward declaration; assigned after the TX characteristic is created.
    push_reply = lambda _line: None

    def handle_command(text):
        cmd = text.strip().upper()
        if not cmd:
            return
        if cmd in ('QUIT', 'EXIT'):
            push_reply('BYE')
            return
        if cmd == 'STATUS':
            push_reply('OK: serial open' if link.is_open() else 'OK: serial closed')
            return
        byte = COMMAND_MAP.get(cmd)
        if byte is None:
            push_reply('ERR: Unknown command')
            return
        print(f'[ble] {cmd} -> {byte}')
        if link.send_byte(byte):
            push_reply(f'OK: {cmd}')
        else:
            push_reply('ERR: serial write failed')

    def rx_write(value, options):
        text = bytes(value).decode(errors='replace')
        for part in text.replace('\r', '\n').split('\n'):
            if part.strip():
                handle_command(part)

    robot.add_characteristic(srv_id=1, chr_id=1, uuid=NUS_RX_CHAR,
                             value=[], notifying=False,
                             flags=['write', 'write-without-response'],
                             write_callback=rx_write)
    robot.add_characteristic(srv_id=1, chr_id=2, uuid=NUS_TX_CHAR,
                             value=[], notifying=False, flags=['notify'])

    tx_char = robot.characteristics[-1]

    def _push_reply(line):
        try:
            tx_char.set_value(list((line + '\n').encode()))
        except Exception:
            pass
    push_reply = _push_reply

    # Register the auto-accept pairing agent before publishing the GATT
    # peripheral.  Once published, BlueZ will start handling pair requests
    # via this agent — no prompt on the Pi.  Keep a reference around so
    # dbus-python doesn't garbage-collect it (the agent is a live D-Bus
    # service object, not just a configuration value).
    if not args.no_auto_accept:
        try:
            _agent = register_auto_accept_agent()  # noqa: F841 (kept alive intentionally)
        except Exception as e:
            print(f'WARNING: could not register auto-accept pairing agent: {e}', file=sys.stderr)
            print('         Pairing will fall back to whatever default agent is registered,', file=sys.stderr)
            print('         which on a headless Pi usually means no agent and pairing will fail.', file=sys.stderr)

    print(f'BLE peripheral "{args.name}" advertising the Nordic UART Service on adapter {adapter_addr}.')
    print(f'Forwarding commands directly to Arduino on {args.serial} @ {args.baud} baud.')
    print('Pair from your phone (nRF Connect / Bluefruit Connect / LightBlue). Ctrl-C to stop.')
    try:
        robot.publish()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
