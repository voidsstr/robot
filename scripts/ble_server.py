#!/usr/bin/env python3
"""BLE -> robot command bridge.

Runs *in parallel* with the WiFi control path: it exposes a BLE GATT
peripheral that an iPhone or Android phone can pair with, and forwards every
command it receives to the local WiFi command server (127.0.0.1:8080) — the
same backend `robot wifi-server` / `robot_daemon` use. So BLE control and
WiFi control share the exact same command parser and Arduino serial link.

GATT layout (Nordic UART Service — supported out of the box by nRF Connect,
Adafruit Bluefruit Connect, LightBlue, etc.):

  Service  6E400001-B5A3-F393-E0A9-E50E24DCCA9E
    RX  6E400002-B5A3-F393-E0A9-E50E24DCCA9E   Write             phone -> robot
    TX  6E400003-B5A3-F393-E0A9-E50E24DCCA9E   Notify            robot -> phone

Write one command per BLE write (newline optional). Accepted commands are the
same as the TCP protocol — `UP` / `DOWN` / `LEFT` / `RIGHT` / `STOP` (and the
aliases `FORWARD`, `BACK`, `W`, `A`, `S`, `D`, `X`, `STATUS`). The server's
reply (e.g. `OK: UP`) is pushed back as a TX notification.

Requirements:  BlueZ >= 5.50, python3-dbus, python3-gi, and `bluezero`
  (`pip3 install --break-system-packages bluezero`, or run scripts/install_deps.sh).

Run as root (BlueZ D-Bus + advertising usually needs it):
  sudo python3 scripts/ble_server.py
  sudo python3 scripts/ble_server.py --name MyRobot --port 8080
"""

import argparse
import socket
import sys
import threading
import time

NUS_SERVICE = '6E400001-B5A3-F393-E0A9-E50E24DCCA9E'
NUS_RX_CHAR = '6E400002-B5A3-F393-E0A9-E50E24DCCA9E'   # Write   (phone -> robot)
NUS_TX_CHAR = '6E400003-B5A3-F393-E0A9-E50E24DCCA9E'   # Notify  (robot -> phone)

DEFAULT_NAME = 'RobotBLE'
DEFAULT_CMD_HOST = '127.0.0.1'
DEFAULT_CMD_PORT = 8080


class CommandLink:
    """Persistent, auto-reconnecting TCP link to the WiFi command server."""

    def __init__(self, host, port):
        self._host = host
        self._port = port
        self._sock = None
        self._lock = threading.Lock()
        self.on_reply = None  # callable(str) -> None

    def _connect_locked(self):
        s = socket.create_connection((self._host, self._port), timeout=5)
        s.settimeout(None)
        self._sock = s
        threading.Thread(target=self._reader, args=(s,), daemon=True).start()

    def _reader(self, s):
        buf = b''
        try:
            while True:
                data = s.recv(256)
                if not data:
                    break
                buf += data
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    if self.on_reply:
                        try:
                            self.on_reply(line.decode(errors='replace'))
                        except Exception:
                            pass
        except OSError:
            pass
        finally:
            with self._lock:
                if self._sock is s:
                    self._sock = None

    def send(self, text):
        payload = (text.strip() + '\n').encode()
        with self._lock:
            for _ in range(2):
                try:
                    if self._sock is None:
                        self._connect_locked()
                    self._sock.sendall(payload)
                    return True
                except OSError:
                    self._sock = None
                    time.sleep(0.2)
        print(f'[ble] could not reach command server at {self._host}:{self._port} '
              f'(is `robot wifi-server` / `robot_daemon` running?)', file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description='BLE -> robot command bridge')
    parser.add_argument('--name', default=DEFAULT_NAME, help='BLE advertised name')
    parser.add_argument('--host', default=DEFAULT_CMD_HOST, help='WiFi command server host')
    parser.add_argument('--port', type=int, default=DEFAULT_CMD_PORT, help='WiFi command server port')
    parser.add_argument('--adapter', default=None, help='Bluetooth adapter address (default: first available)')
    args = parser.parse_args()

    try:
        from bluezero import adapter, peripheral
    except ImportError:
        sys.exit('bluezero is not installed. Run: pip3 install --break-system-packages bluezero\n'
                 '(also needs python3-dbus and python3-gi; see scripts/install_deps.sh)')

    adapter_addr = args.adapter
    if adapter_addr is None:
        adapters = list(adapter.Adapter.available())
        if not adapters:
            sys.exit('No Bluetooth adapter found. Try: sudo bluetoothctl power on')
        adapter_addr = adapters[0].address

    link = CommandLink(args.host, args.port)

    robot = peripheral.Peripheral(adapter_addr, local_name=args.name)
    robot.add_service(srv_id=1, uuid=NUS_SERVICE, primary=True)

    def rx_write(value, options):
        text = bytes(value).decode(errors='replace')
        for part in text.replace('\r', '\n').split('\n'):
            part = part.strip()
            if part:
                print(f'[ble] -> {part}')
                link.send(part)

    robot.add_characteristic(srv_id=1, chr_id=1, uuid=NUS_RX_CHAR,
                             value=[], notifying=False,
                             flags=['write', 'write-without-response'],
                             write_callback=rx_write)
    robot.add_characteristic(srv_id=1, chr_id=2, uuid=NUS_TX_CHAR,
                             value=[], notifying=False, flags=['notify'])

    tx_char = robot.characteristics[-1]

    def push_reply(line):
        try:
            tx_char.set_value([b for b in (line + '\n').encode()])
        except Exception:
            pass
    link.on_reply = push_reply

    print(f'BLE peripheral "{args.name}" advertising the Nordic UART Service on adapter {adapter_addr}.')
    print(f'Forwarding commands to {args.host}:{args.port}. Pair from your phone (nRF Connect / Bluefruit Connect / LightBlue).')
    print('Ctrl-C to stop.')
    try:
        robot.publish()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
