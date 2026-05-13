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


def install_disconnect_safety_stop(link, on_connect=None, on_disconnect=None):
    """Subscribe to BlueZ D-Bus signals and fire a STOP byte to the Arduino
    the instant any device disconnects from us.

    This is our PRIMARY defensive measure: when the BLE link drops mid-drive
    (phone out of range / battery died / app crashed), the Arduino would
    otherwise keep emitting the last commanded servo pulse forever and the
    robot would keep going.  Catching the disconnect at the BlueZ layer
    means we send neutral within a few hundred ms of the link going away.

    The on_connect / on_disconnect callbacks (if provided) are also called
    so we can log connection state and reset the activity watchdog.
    """
    import dbus

    bus = dbus.SystemBus()

    # PropertiesChanged on org.bluez.Device1 fires whenever a remote
    # device's Connected/Paired/RSSI/etc properties change.  We only care
    # about Connected transitioning to false.
    def _on_props_changed(interface, changed, invalidated, path=None):
        if interface != 'org.bluez.Device1':
            return
        if 'Connected' not in changed:
            return
        connected = bool(changed['Connected'])
        if connected:
            print(f'[ble] device CONNECTED  ({path})')
            if on_connect:
                try: on_connect(path)
                except Exception as e: print(f'[ble] on_connect cb error: {e}', file=sys.stderr)
        else:
            print(f'[ble] device DISCONNECTED ({path}) — sending STOP to Arduino')
            # Send neutral immediately; the Arduino's destructor-equivalent
            # behaviour is only to hold the last pulse, so without this the
            # robot would coast indefinitely.
            try:
                link.send_byte('S')
            except Exception as e:
                print(f'[ble] could not send safety STOP: {e}', file=sys.stderr)
            if on_disconnect:
                try: on_disconnect(path)
                except Exception as e: print(f'[ble] on_disconnect cb error: {e}', file=sys.stderr)

    bus.add_signal_receiver(
        _on_props_changed,
        signal_name='PropertiesChanged',
        dbus_interface='org.freedesktop.DBus.Properties',
        bus_name='org.bluez',
        path_keyword='path')

    print('Disconnect safety-stop installed (fires "S" on any BLE disconnect).')


class ActivityWatchdog:
    """Optional fall-back timeout: if no command has come in for `timeout_secs`,
    send a STOP byte.  Disabled by default because it conflicts with the
    'hold speed when you release the button' UX we deliberately built into
    the iOS app — but useful for unattended operation or when the app's
    auto-repeat hiccups.  Opt in with `--watchdog SECS`.

    Only fires while a BLE device is connected (tracked via set_connected())
    so it doesn't keep nagging the Arduino while nothing is paired.
    """

    def __init__(self, link, timeout_secs):
        self._link = link
        self._timeout = float(timeout_secs)
        self._last = time.monotonic()
        self._connected = False
        self._triggered = False
        self._stop_flag = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='ble-watchdog')

    def start(self):
        self._thread.start()
        print(f'Activity watchdog armed: STOP after {self._timeout:.1f}s of silence while connected.')

    def stop(self):
        self._stop_flag = True

    def feed(self):
        with self._lock:
            self._last = time.monotonic()
            self._triggered = False

    def set_connected(self, connected):
        with self._lock:
            self._connected = bool(connected)
            self._last = time.monotonic()    # reset on connect
            self._triggered = False

    def _run(self):
        # Check four times per second; cheap and gives a good worst-case
        # latency of ~250 ms beyond the configured timeout.
        while not self._stop_flag:
            time.sleep(0.25)
            with self._lock:
                if not self._connected or self._triggered:
                    continue
                age = time.monotonic() - self._last
                if age <= self._timeout:
                    continue
                self._triggered = True
            print(f'[ble] WATCHDOG: {age:.1f}s of silence, sending STOP to Arduino')
            try:
                self._link.send_byte('S')
            except Exception as e:
                print(f'[ble] watchdog STOP failed: {e}', file=sys.stderr)


class AdapterHealthMonitor:
    """Background poll on the Bluetooth adapter's `Powered` D-Bus property.
    If the adapter goes down (rfkill, driver glitch, etc.) we can't recover
    in-process — re-publishing the peripheral usually wedges BlueZ — so we
    exit with a non-zero status.  systemd's Restart=always on the unit then
    brings us back, fresh, after RestartSec.  Returns the running thread."""

    def __init__(self, adapter_addr, period=10.0):
        self._addr = adapter_addr
        self._period = period
        self._stop_flag = False
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='ble-adapter-health')

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop_flag = True

    def _run(self):
        import dbus
        bus = dbus.SystemBus()
        adapter_path = '/org/bluez/' + self._addr.replace(':', '_').replace('00_', 'hci')
        # Above heuristic is fragile across BlueZ versions; do it properly:
        # find the path by matching adapter Address property.
        try:
            mgr = dbus.Interface(
                bus.get_object('org.bluez', '/'),
                'org.freedesktop.DBus.ObjectManager')
            for path, ifaces in mgr.GetManagedObjects().items():
                if 'org.bluez.Adapter1' in ifaces:
                    if str(ifaces['org.bluez.Adapter1'].get('Address')) == self._addr:
                        adapter_path = path
                        break
        except Exception:
            pass

        while not self._stop_flag:
            time.sleep(self._period)
            try:
                obj = bus.get_object('org.bluez', adapter_path)
                props = dbus.Interface(obj, 'org.freedesktop.DBus.Properties')
                powered = bool(props.Get('org.bluez.Adapter1', 'Powered'))
                if not powered:
                    print('FATAL: bluetooth adapter is no longer Powered. Exiting '
                          'so systemd can restart us.', file=sys.stderr)
                    # Use os._exit to skip Python finalisers that may hang
                    # on a wedged D-Bus connection.
                    import os
                    os._exit(2)
            except Exception as e:
                print(f'FATAL: adapter health check failed ({e}). Exiting so '
                      'systemd can restart us.', file=sys.stderr)
                import os
                os._exit(3)


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
    parser.add_argument('--watchdog', type=float, default=0.0, metavar='SECS',
                        help="if SECS > 0, send STOP to the Arduino after that many seconds "
                             "of silence from the connected phone.  Off by default (0) so the "
                             "robot 'holds speed when you release the button' like the keyboard. "
                             "The disconnect-triggered safety STOP is ALWAYS on regardless.")
    parser.add_argument('--no-disconnect-stop', action='store_true',
                        help="disable the disconnect-triggered safety STOP.  Not recommended "
                             "for tank-style robots — without this, BLE drops mid-drive leave "
                             "the robot coasting indefinitely.  Provided as an escape hatch "
                             "only.")
    parser.add_argument('--no-adapter-watchdog', action='store_true',
                        help="don't monitor the BT adapter's Powered state.  By default we "
                             "exit (non-zero) when the adapter goes down so systemd can "
                             "restart us fresh.")
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

    # Forward-declared so handle_command can call it before we instantiate
    # the watchdog further down.  Re-assigned at startup if --watchdog > 0.
    feed_watchdog = lambda: None

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
        feed_watchdog()                  # mark activity for the timeout watchdog
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

    # Spin up the activity watchdog (opt-in via --watchdog).  set_connected()
    # below is wired into the BlueZ disconnect monitor so the watchdog only
    # nags us while a phone is actually paired and connected.
    watchdog = None
    if args.watchdog > 0:
        watchdog = ActivityWatchdog(link, args.watchdog)
        watchdog.start()
        feed_watchdog = watchdog.feed   # noqa: F811 — replace the no-op forward-decl

    # Install the disconnect safety-stop unless explicitly disabled.  This is
    # the PRIMARY defensive measure for tank-style robots: BLE drops →
    # Arduino gets 'S' → motors return to neutral within a few hundred ms.
    if not args.no_disconnect_stop:
        def _on_connect(_path):
            if watchdog: watchdog.set_connected(True)
        def _on_disconnect(_path):
            if watchdog: watchdog.set_connected(False)
        try:
            install_disconnect_safety_stop(link, on_connect=_on_connect,
                                           on_disconnect=_on_disconnect)
        except Exception as e:
            print(f'WARNING: could not install disconnect safety stop: {e}', file=sys.stderr)
            print('         BLE drops mid-drive will not auto-stop the robot.', file=sys.stderr)

    # Adapter-health monitor — exits non-zero if the BT adapter goes away so
    # systemd's Restart=always brings us back fresh.  In-process recovery is
    # unreliable (BlueZ tends to wedge after rfkill); a clean restart works.
    if not args.no_adapter_watchdog:
        try:
            AdapterHealthMonitor(adapter_addr).start()
        except Exception as e:
            print(f'WARNING: could not start adapter health monitor: {e}', file=sys.stderr)

    print(f'BLE peripheral "{args.name}" advertising the Nordic UART Service on adapter {adapter_addr}.')
    print(f'Forwarding commands directly to Arduino on {args.serial} @ {args.baud} baud.')
    print('Pair from your phone (nRF Connect / Bluefruit Connect / LightBlue). Ctrl-C to stop.')
    try:
        robot.publish()
    except KeyboardInterrupt:
        pass
    finally:
        # On graceful shutdown, park the robot too.  Belt-and-braces:
        # the disconnect handler should already have done this when BlueZ
        # tore down the GATT server, but if we're stopping with a
        # connected phone, that handler won't fire on our way out.
        try:
            link.send_byte('S')
        except Exception:
            pass
        if watchdog:
            watchdog.stop()


if __name__ == '__main__':
    main()
