#!/usr/bin/env python3
"""Terminal arrow-key drive: TCP client to bin/robot wifi-server.

A lightweight stdlib-only alternative to bin/wifi_client. Drive feel is
modeled on scripts/web_drive.py: hold a direction key to drive, release
to auto-STOP after a short idle window. Works over SSH; no ncurses, no
compile step.

Usage:
    # On the Pi, start the wifi-server:
    ./bin/robot wifi-server -p 8080

    # From any LAN machine with Python 3:
    python3 scripts/terminal_drive.py <pi-host> [--port 8080]

Keys:
    Arrow keys / WASD   drive (hold to keep moving)
    Space               explicit STOP
    ?                   STATUS query
    q / Ctrl-C          quit (sends STOP first)
"""

from __future__ import annotations

import argparse
import os
import select
import socket
import sys
import termios
import threading
import time
import tty


# Keep the wire protocol in sync with WifiCommandServer / web_drive.py.
ALLOWED_CMDS = {'UP', 'DOWN', 'LEFT', 'RIGHT', 'STOP', 'STATUS'}

# ~8 Hz repeat while a direction key is held; the Arduino steps motors
# by +/-3 per command, so this gives a smooth ramp without flooding the
# socket. Matches web_drive.py's REPEAT_MS.
REPEAT_S = 0.120

# If no key event arrives for this long after the last direction press,
# treat the key as released and send STOP. Terminals don't emit keyup
# events; auto-repeat of a held key keeps refreshing this deadline.
IDLE_STOP_S = 0.250


class CommandClient:
    """Single persistent TCP connection to bin/robot wifi-server.

    The wifi-server is single-client, so we hold one socket open for
    the lifetime of the session and serialize sends behind a lock. On
    a transport error the next send_cmd() reconnects once before
    giving up."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def _connect_locked(self):
        s = socket.create_connection((self._host, self._port), timeout=3.0)
        s.settimeout(2.0)
        try:
            s.recv(256)  # drain ROBOT READY greeting
        except (socket.timeout, OSError):
            pass
        self._sock = s

    def send_cmd(self, cmd: str) -> tuple[bool, str]:
        cmd = cmd.strip().upper()
        if cmd not in ALLOWED_CMDS:
            return False, f'ERR: unknown command {cmd!r}'
        payload = (cmd + '\n').encode('ascii')
        last_err: Exception | None = None
        with self._lock:
            for _ in range(2):
                try:
                    if self._sock is None:
                        self._connect_locked()
                    assert self._sock is not None
                    self._sock.sendall(payload)
                    try:
                        reply = self._sock.recv(256).decode('ascii', errors='replace').strip()
                    except socket.timeout:
                        reply = ''
                    return True, reply or 'OK'
                except Exception as e:
                    last_err = e
                    try:
                        if self._sock is not None:
                            self._sock.close()
                    except Exception:
                        pass
                    self._sock = None
            return False, f'ERR: {last_err}'

    def close(self):
        with self._lock:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None


def _read_key(timeout_s: float) -> str | None:
    """Block up to timeout_s for one key. Returns a logical key name:
    'UP'/'DOWN'/'LEFT'/'RIGHT' for arrows, 'SPACE', 'Q', '?', 'W'/'A'/
    'S'/'D' for letters, or None on timeout.

    Arrow keys arrive as a 3-byte CSI escape (ESC '[' 'A'..'D'). We
    consume the whole sequence in one call so the caller sees a single
    logical event."""
    r, _, _ = select.select([sys.stdin], [], [], timeout_s)
    if not r:
        return None
    ch = os.read(sys.stdin.fileno(), 1)
    if not ch:
        return None
    if ch == b'\x1b':
        # ESC alone vs CSI: peek with a tiny timeout so a bare ESC
        # (Alt-key, real ESC) doesn't hang waiting for more bytes.
        r2, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not r2:
            return 'ESC'
        b1 = os.read(sys.stdin.fileno(), 1)
        if b1 != b'[':
            return 'ESC'
        r3, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not r3:
            return 'ESC'
        b2 = os.read(sys.stdin.fileno(), 1)
        return {b'A': 'UP', b'B': 'DOWN', b'C': 'RIGHT', b'D': 'LEFT'}.get(b2)
    if ch == b' ':
        return 'SPACE'
    if ch in (b'\x03', b'\x04'):  # Ctrl-C, Ctrl-D
        return 'QUIT'
    try:
        c = ch.decode('ascii').upper()
    except UnicodeDecodeError:
        return None
    if c in ('W', 'A', 'S', 'D', 'Q', '?'):
        return c
    return None


KEY_TO_CMD = {
    'UP': 'UP', 'W': 'UP',
    'DOWN': 'DOWN', 'S': 'DOWN',
    'LEFT': 'LEFT', 'A': 'LEFT',
    'RIGHT': 'RIGHT', 'D': 'RIGHT',
    'SPACE': 'STOP',
    '?': 'STATUS',
}


def _redraw(host: str, port: int, connected: bool, active: str | None,
            last_cmd: str, last_reply: str):
    # Single-line status, redrawn in place. CR + clear-to-EOL.
    conn = '\x1b[32mok\x1b[0m' if connected else '\x1b[31mdown\x1b[0m'
    active_disp = active if active else '-'
    sys.stdout.write(
        f'\r\x1b[2K{host}:{port} [{conn}]  active: {active_disp:<5}  '
        f'last: {last_cmd:<6}  reply: {last_reply[:40]}'
    )
    sys.stdout.flush()


def drive_loop(client: CommandClient, host: str, port: int):
    active: str | None = None       # currently held direction (logical key)
    active_until = 0.0              # auto-STOP deadline
    next_repeat = 0.0               # next time to resend the held cmd
    last_cmd = '-'
    last_reply = '-'
    connected = True

    sys.stdout.write('\n')          # leave room for the status line
    _redraw(host, port, connected, active, last_cmd, last_reply)

    try:
        while True:
            now = time.monotonic()

            # Decide how long to wait for the next key. If something
            # is held, we need to wake up to repeat or to auto-STOP,
            # whichever comes first.
            if active is None:
                wait = 1.0
            else:
                wait = max(0.0, min(active_until, next_repeat) - now)

            key = _read_key(wait)
            now = time.monotonic()

            if key == 'QUIT' or key == 'Q':
                client.send_cmd('STOP')
                sys.stdout.write('\n')
                return

            if key is not None:
                cmd = KEY_TO_CMD.get(key)
                if cmd is not None:
                    if cmd in ('STOP', 'STATUS'):
                        ok, reply = client.send_cmd(cmd)
                        last_cmd, last_reply, connected = cmd, reply, ok
                        if cmd == 'STOP':
                            active = None
                    else:
                        # Direction key. If a new direction, send
                        # immediately; either way refresh the hold
                        # deadline and repeat schedule.
                        if active != key:
                            ok, reply = client.send_cmd(cmd)
                            last_cmd, last_reply, connected = cmd, reply, ok
                            next_repeat = now + REPEAT_S
                        active = key
                        active_until = now + IDLE_STOP_S

            # Time-based actions even when no key arrived.
            if active is not None:
                if now >= active_until:
                    ok, reply = client.send_cmd('STOP')
                    last_cmd, last_reply, connected = 'STOP', reply, ok
                    active = None
                elif now >= next_repeat:
                    cmd = KEY_TO_CMD[active]
                    ok, reply = client.send_cmd(cmd)
                    last_cmd, last_reply, connected = cmd, reply, ok
                    next_repeat = now + REPEAT_S

            _redraw(host, port, connected, active, last_cmd, last_reply)
    except KeyboardInterrupt:
        client.send_cmd('STOP')
        sys.stdout.write('\n')


def main():
    p = argparse.ArgumentParser(description='Terminal arrow-key drive for the robot wifi-server.')
    p.add_argument('host', help='Pi hostname or IP running bin/robot wifi-server.')
    p.add_argument('--port', type=int, default=8080, help='wifi-server TCP port (default 8080).')
    args = p.parse_args()

    if not sys.stdin.isatty():
        sys.exit('terminal_drive: stdin is not a TTY (need an interactive terminal).')

    client = CommandClient(args.host, args.port)
    ok, reply = client.send_cmd('STATUS')
    if ok:
        print(f'connected to {args.host}:{args.port} — {reply}', file=sys.stderr)
    else:
        print(f'WARN: could not reach wifi-server at {args.host}:{args.port} ({reply}).',
              file=sys.stderr)
        print(f'      Start it on the Pi with: ./bin/robot wifi-server -p {args.port}',
              file=sys.stderr)

    print('Arrow keys / WASD = drive (hold). Space = STOP. ? = STATUS. q = quit.',
          file=sys.stderr)

    fd = sys.stdin.fileno()
    old_attrs = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        drive_loop(client, args.host, args.port)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
        client.close()


if __name__ == '__main__':
    main()
