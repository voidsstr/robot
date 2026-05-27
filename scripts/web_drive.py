#!/usr/bin/env python3
"""Browser-based drive UI: live MJPEG camera stream + arrow-key control.

Open http://<pi-ip>:8090/ in any browser on the LAN. Hold ↑ ↓ ← → (or
W/A/S/D) to drive; release to stop. Spacebar = emergency stop. The page
shows the live camera feed and the last command sent.

Architecture (mirrors scripts/wifi_desktop.py — the Pi-desktop GUI):

  Browser  ──HTTP──►  web_drive.py  ──TCP :8080──►  bin/robot wifi-server
                          │                              │
                          │                              ▼  USB
                          │                          Arduino ─► Sabertooth
                          │
                          ▼  picamera2
                       IMX519 camera

So this is a drop-in replacement for the wifi_client terminal UI, but
served as a webpage — handy for driving from a phone or a laptop without
a terminal. It reuses CameraSource from ble_server.py, so the camera
must not be in use by another process (stop robot-ble.service first).

Usage:
    # On the Pi, alongside bin/robot wifi-server:
    sudo systemctl stop robot-ble        # free /dev/video* + the lidar
    ./bin/robot wifi-server -p 8080 &    # owns /dev/ttyACM0
    python3 scripts/web_drive.py         # serves on :8090

    # Then on any device on the LAN:
    open http://<pi-ip>:8090/
"""

from __future__ import annotations

import argparse
import http.server
import io
import os
import socket
import socketserver
import sys
import threading
import time
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from ble_server import CameraSource  # type: ignore
except Exception as e:
    sys.exit(f'Could not import CameraSource from ble_server.py: {e}')


# Commands the WiFi server accepts. Keep this allow-list small so the
# HTTP endpoint can't be used to inject anything weird.
ALLOWED_CMDS = {'UP', 'DOWN', 'LEFT', 'RIGHT', 'STOP', 'STATUS'}


class CommandClient:
    """Persistent TCP client to bin/robot wifi-server.

    The wifi-server accepts one client at a time, so we hold a single
    socket open for the whole web_drive lifetime and serialise writes
    behind a lock. If the connection drops, the next send_cmd() tries
    to reconnect once before returning False."""

    def __init__(self, host: str, port: int):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def _connect_locked(self):
        s = socket.create_connection((self._host, self._port), timeout=3.0)
        s.settimeout(2.0)
        # Drain the server's greeting (ROBOT READY / ...).
        try:
            s.recv(256)
        except (socket.timeout, OSError):
            pass
        self._sock = s

    def send_cmd(self, cmd: str) -> tuple[bool, str]:
        cmd = cmd.strip().upper()
        if cmd not in ALLOWED_CMDS:
            return False, f'ERR: unknown command {cmd!r}'
        payload = (cmd + '\n').encode('ascii')
        with self._lock:
            for attempt in range(2):
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
                    try:
                        if self._sock is not None:
                            self._sock.close()
                    except Exception:
                        pass
                    self._sock = None
                    last_err = e
            return False, f'ERR: {last_err}'


# --------------------------------------------------------------------------- #
# HTML page
# --------------------------------------------------------------------------- #
INDEX_HTML = b"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, user-scalable=no">
<title>Robot Drive</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; padding: 0; background: #020617; color: #e2e8f0;
               font-family: -apple-system, system-ui, Helvetica, Arial, sans-serif; }
  .wrap { max-width: 960px; margin: 0 auto; padding: 16px; }
  h1 { margin: 0 0 8px 0; font-size: 18px; color: #94a3b8; font-weight: 500; }
  .stream { width: 100%; background: #0f172a; border-radius: 8px;
            aspect-ratio: 4 / 3; object-fit: contain; display: block; }
  .row { display: flex; gap: 12px; align-items: center; margin-top: 12px; flex-wrap: wrap; }
  .pill { background: #0f172a; padding: 6px 12px; border-radius: 999px;
          font-size: 13px; color: #cbd5e1; }
  .pill b { color: #fbbf24; }
  .hint { color: #64748b; font-size: 13px; }
  .pad { display: grid; grid-template-columns: repeat(3, 60px);
         grid-template-rows: repeat(3, 60px); gap: 6px; margin-top: 16px;
         user-select: none; -webkit-user-select: none; touch-action: none; }
  .pad button { background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
                border-radius: 8px; font-size: 24px; cursor: pointer;
                font-family: inherit; }
  .pad button:active, .pad button.active { background: #2563eb; border-color: #3b82f6; }
  .pad .up    { grid-column: 2; grid-row: 1; }
  .pad .left  { grid-column: 1; grid-row: 2; }
  .pad .stop  { grid-column: 2; grid-row: 2; font-size: 14px; }
  .pad .right { grid-column: 3; grid-row: 2; }
  .pad .down  { grid-column: 2; grid-row: 3; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Robot Drive</h1>
  <img id="stream" class="stream" src="/stream.mjpg" alt="camera">
  <div class="row">
    <span class="pill">last: <b id="last">&mdash;</b></span>
    <span class="pill">reply: <span id="reply">&mdash;</span></span>
    <span class="hint">Arrow keys / WASD = drive &middot; Space = stop</span>
  </div>
  <div class="pad" id="pad">
    <button class="up"    data-cmd="UP">&#9650;</button>
    <button class="left"  data-cmd="LEFT">&#9664;</button>
    <button class="stop"  data-cmd="STOP">STOP</button>
    <button class="right" data-cmd="RIGHT">&#9654;</button>
    <button class="down"  data-cmd="DOWN">&#9660;</button>
  </div>
</div>
<script>
(() => {
  const KEY_MAP = {
    ArrowUp: 'UP', KeyW: 'UP',
    ArrowDown: 'DOWN', KeyS: 'DOWN',
    ArrowLeft: 'LEFT', KeyA: 'LEFT',
    ArrowRight: 'RIGHT', KeyD: 'RIGHT',
    Space: 'STOP',
  };
  // Arduino steps motors by +/-3 per command; ~8 Hz feels like a smooth
  // ramp without flooding the TCP socket.
  const REPEAT_MS = 120;
  let activeCmd = null;
  let repeatTimer = null;
  const lastEl  = document.getElementById('last');
  const replyEl = document.getElementById('reply');

  async function send(cmd) {
    lastEl.textContent = cmd;
    try {
      const r = await fetch('/cmd?c=' + encodeURIComponent(cmd), { method: 'POST' });
      replyEl.textContent = (await r.text()).trim() || (r.ok ? 'OK' : 'ERR');
    } catch (e) {
      replyEl.textContent = 'net err';
    }
  }

  function start(cmd) {
    if (activeCmd === cmd) return;
    stop();
    activeCmd = cmd;
    highlight(cmd, true);
    send(cmd);
    if (cmd !== 'STOP') {
      repeatTimer = setInterval(() => send(cmd), REPEAT_MS);
    }
  }

  function stop() {
    if (repeatTimer) { clearInterval(repeatTimer); repeatTimer = null; }
    if (activeCmd && activeCmd !== 'STOP') {
      highlight(activeCmd, false);
      send('STOP');
    } else if (activeCmd === 'STOP') {
      highlight('STOP', false);
    }
    activeCmd = null;
  }

  function highlight(cmd, on) {
    const btn = document.querySelector(`#pad button[data-cmd="${cmd}"]`);
    if (btn) btn.classList.toggle('active', on);
  }

  document.addEventListener('keydown', (e) => {
    if (e.repeat) return;
    const cmd = KEY_MAP[e.code];
    if (!cmd) return;
    e.preventDefault();
    start(cmd);
  });
  document.addEventListener('keyup', (e) => {
    const cmd = KEY_MAP[e.code];
    if (!cmd) return;
    e.preventDefault();
    if (activeCmd === cmd) stop();
  });
  // Releasing the window (alt-tab, click outside): fail safe to STOP.
  window.addEventListener('blur', stop);

  // Touch / mouse on the on-screen pad.
  document.querySelectorAll('#pad button').forEach(btn => {
    const cmd = btn.dataset.cmd;
    const begin = (e) => { e.preventDefault(); start(cmd); };
    const end   = (e) => { e.preventDefault(); if (activeCmd === cmd) stop(); };
    btn.addEventListener('pointerdown', begin);
    btn.addEventListener('pointerup', end);
    btn.addEventListener('pointercancel', end);
    btn.addEventListener('pointerleave', end);
  });

  // If the MJPEG stream stalls, force a reload after a few seconds.
  const img = document.getElementById('stream');
  img.addEventListener('error', () => {
    setTimeout(() => { img.src = '/stream.mjpg?t=' + Date.now(); }, 2000);
  });
})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
class DriveHandler(http.server.BaseHTTPRequestHandler):
    # Class attributes injected by main(): camera, cmd_client, jpeg_quality, mjpeg_fps.

    server_version = 'RobotWebDrive/1.0'

    def log_message(self, fmt, *args):  # less noisy than the default
        sys.stderr.write('[web] %s - %s\n' % (self.address_string(), fmt % args))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/' or path == '/index.html':
            self._send_bytes(200, 'text/html; charset=utf-8', INDEX_HTML)
        elif path == '/stream.mjpg':
            self._stream_mjpeg()
        elif path == '/healthz':
            self._send_bytes(200, 'text/plain', b'ok\n')
        else:
            self._send_bytes(404, 'text/plain', b'not found\n')

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != '/cmd':
            self._send_bytes(404, 'text/plain', b'not found\n')
            return
        # Accept ?c=UP or a plain-text body. ?c= is what the JS uses; the
        # plain-body form is here for ad-hoc curl testing.
        cmd = None
        for kv in (parsed.query or '').split('&'):
            if kv.startswith('c='):
                cmd = kv[2:]
                break
        if cmd is None:
            try:
                n = int(self.headers.get('Content-Length') or 0)
            except ValueError:
                n = 0
            cmd = self.rfile.read(n).decode('ascii', errors='replace') if n else ''
        ok, reply = self.server.cmd_client.send_cmd(cmd)
        body = (reply + '\n').encode('utf-8', errors='replace')
        self._send_bytes(200 if ok else 502, 'text/plain; charset=utf-8', body)

    def _send_bytes(self, status: int, ctype: str, body: bytes):
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _stream_mjpeg(self):
        camera: CameraSource = self.server.camera  # type: ignore[attr-defined]
        if camera is None or not camera.available:
            self._send_bytes(503, 'text/plain', b'camera unavailable\n')
            return

        boundary = b'frame'
        self.send_response(200)
        self.send_header('Age', '0')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=' + boundary.decode())
        self.end_headers()

        period = 1.0 / max(1, self.server.mjpeg_fps)
        try:
            while True:
                t0 = time.monotonic()
                jpeg = camera.capture_video_jpeg()
                if not jpeg:
                    time.sleep(0.1)
                    continue
                hdr = (b'--' + boundary + b'\r\n'
                       b'Content-Type: image/jpeg\r\n'
                       b'Content-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n')
                self.wfile.write(hdr)
                self.wfile.write(jpeg)
                self.wfile.write(b'\r\n')
                dt = time.monotonic() - t0
                if dt < period:
                    time.sleep(period - dt)
        except (BrokenPipeError, ConnectionResetError):
            return


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(description='Browser drive UI: live MJPEG + arrow-key control.')
    p.add_argument('--host', default='0.0.0.0', help='HTTP bind address (default: 0.0.0.0).')
    p.add_argument('--port', type=int, default=8090, help='HTTP port (default: 8090).')
    p.add_argument('--cmd-host', default='127.0.0.1',
                   help='Host of bin/robot wifi-server (default: 127.0.0.1).')
    p.add_argument('--cmd-port', type=int, default=8080,
                   help='Port of bin/robot wifi-server (default: 8080).')
    p.add_argument('--camera-width', type=int, default=640)
    p.add_argument('--camera-height', type=int, default=480)
    p.add_argument('--camera-fps', type=int, default=10,
                   help='MJPEG stream frame rate (default: 10).')
    p.add_argument('--camera-quality', type=int, default=75,
                   help='JPEG quality, 1-95 (default: 75).')
    p.add_argument('--no-camera', action='store_true',
                   help='Disable the camera (control-only page).')
    args = p.parse_args()

    camera = None
    if not args.no_camera:
        camera = CameraSource(
            video_size=(args.camera_width, args.camera_height),
            photo_size=(1280, 960),
            video_quality=args.camera_quality,
            photo_quality=85,
        )
        if not camera.available:
            print('[web] camera unavailable — the page will load but the video tile will be blank.',
                  file=sys.stderr)

    cmd_client = CommandClient(args.cmd_host, args.cmd_port)
    # Probe once so the user sees the connection state at startup; not fatal
    # if it fails — the client will keep retrying on each command.
    ok, reply = cmd_client.send_cmd('STATUS')
    if ok:
        print(f'[web] connected to wifi-server at {args.cmd_host}:{args.cmd_port}: {reply}',
              file=sys.stderr)
    else:
        print(f'[web] wifi-server not reachable at {args.cmd_host}:{args.cmd_port} ({reply}). '
              f'Start it with: ./bin/robot wifi-server -p {args.cmd_port}', file=sys.stderr)

    server = ThreadedHTTPServer((args.host, args.port), DriveHandler)
    server.camera = camera                                          # type: ignore[attr-defined]
    server.cmd_client = cmd_client                                  # type: ignore[attr-defined]
    server.mjpeg_fps = max(1, min(30, int(args.camera_fps)))        # type: ignore[attr-defined]

    print(f'[web] serving http://{args.host}:{args.port}/  (Ctrl-C to stop)', file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('[web] shutting down.', file=sys.stderr)
    finally:
        server.server_close()
        if camera is not None:
            camera.close()


if __name__ == '__main__':
    main()
