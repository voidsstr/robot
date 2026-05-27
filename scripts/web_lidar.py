#!/usr/bin/env python3
"""Browser-based lidar viewer: live polar plot of the RPLidar scan.

Open http://<pi-ip>:8091/ in any browser on the LAN.  The page shows a
top-down polar plot with the robot at the centre, range rings every
metre, and obstacle returns drawn as dots.  Scans stream from the Pi to
the browser over Server-Sent Events (~5-10 scans/sec at A1 defaults).

Architecture (mirrors scripts/web_drive.py):

  Browser  ──HTTP/SSE──►  web_lidar.py  ──serial──►  RPLidar A1
                              │
                              ▼  rplidar
                          LidarSource

Reuses LidarSource from scripts/ble_server.py, so the lidar must not be
in use by another process (stop robot-ble.service and any running
wifi_desktop.py first).

Usage:
    sudo systemctl stop robot-ble
    python3 scripts/web_lidar.py        # serves on :8091

    # Then on any device on the LAN:
    open http://<pi-ip>:8091/
"""

from __future__ import annotations

import argparse
import http.server
import json
import os
import queue
import socketserver
import sys
import threading
import time
from urllib.parse import urlparse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from ble_server import LidarSource  # type: ignore
except Exception as e:
    sys.exit(f'Could not import LidarSource from ble_server.py: {e}')


# Treat returns shorter than this as chassis/specular noise, longer than
# this as out of the A1's useful range.  Matches wifi_desktop.py.
LIDAR_MIN_MM = 100
LIDAR_MAX_MM = 12000


class ScanBroker:
    """Fans out lidar scans to every connected SSE client.

    Each client gets its own size-1 latest-wins queue so a slow browser
    can't stall the lidar thread or other clients."""

    def __init__(self):
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._latest: bytes | None = None
        self._scan_count = 0

    def register(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1)
        with self._lock:
            self._clients.append(q)
            # Prime the new client with the most recent scan, if any, so
            # they see something immediately instead of a blank canvas.
            if self._latest is not None:
                try:
                    q.put_nowait(self._latest)
                except queue.Full:
                    pass
        return q

    def unregister(self, q: queue.Queue):
        with self._lock:
            try:
                self._clients.remove(q)
            except ValueError:
                pass

    def publish(self, pts):
        # Filter to useful range and pack as compact JSON.  Angle in
        # degrees (0..360), distance in mm.
        filtered = [
            (round(a, 1), d)
            for (a, d) in pts
            if LIDAR_MIN_MM <= d <= LIDAR_MAX_MM
        ]
        self._scan_count += 1
        payload = json.dumps({
            'id': self._scan_count,
            't': time.time(),
            'n': len(filtered),
            'points': filtered,
        }).encode('utf-8')
        # SSE frame: "data: <json>\n\n"
        frame = b'data: ' + payload + b'\n\n'
        with self._lock:
            self._latest = frame
            for q in self._clients:
                # Latest-wins: drop the stale frame if the client hasn't
                # drained it yet.
                if q.full():
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                try:
                    q.put_nowait(frame)
                except queue.Full:
                    pass

    @property
    def scan_count(self) -> int:
        return self._scan_count

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._clients)


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Robot Lidar</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; padding: 0; background: #020617; color: #e2e8f0;
               font-family: -apple-system, system-ui, Helvetica, Arial, sans-serif; }
  .wrap { max-width: 720px; margin: 0 auto; padding: 16px; }
  h1 { margin: 0 0 8px 0; font-size: 18px; color: #94a3b8; font-weight: 500; }
  .row { display: flex; gap: 12px; align-items: center; margin: 8px 0; flex-wrap: wrap; }
  .pill { background: #0f172a; padding: 6px 12px; border-radius: 999px;
          font-size: 13px; color: #cbd5e1; }
  .pill b { color: #fbbf24; }
  canvas { width: 100%; max-width: 680px; aspect-ratio: 1 / 1;
           background: #0f172a; border-radius: 8px; display: block; }
  .hint { color: #64748b; font-size: 13px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>Robot Lidar</h1>
  <canvas id="plot" width="680" height="680"></canvas>
  <div class="row">
    <span class="pill">scan <b id="scan">&mdash;</b></span>
    <span class="pill">points <b id="pts">&mdash;</b></span>
    <span class="pill">rate <b id="hz">&mdash;</b> Hz</span>
    <span class="pill" id="status">connecting&hellip;</span>
  </div>
  <div class="hint">Robot sits at the centre, looking up. Range rings every 1 m. Max plot range 6 m.</div>
</div>
<script>
(() => {
  const PLOT_MAX_M = 6.0;        // outer edge of canvas
  const RING_STEP_M = 1.0;       // one ring per metre
  const canvas = document.getElementById('plot');
  const ctx = canvas.getContext('2d');
  const scanEl = document.getElementById('scan');
  const ptsEl  = document.getElementById('pts');
  const hzEl   = document.getElementById('hz');
  const statusEl = document.getElementById('status');

  // Track scans/sec over a sliding window for the Hz pill.
  const recent = [];

  function resizeCanvas() {
    // Render at device-pixel resolution for crisp dots/lines.
    const cssSize = canvas.clientWidth;
    const dpr = window.devicePixelRatio || 1;
    canvas.width  = Math.round(cssSize * dpr);
    canvas.height = Math.round(cssSize * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  resizeCanvas();
  window.addEventListener('resize', resizeCanvas);

  function drawScan(scan) {
    const cssSize = canvas.clientWidth;
    const cx = cssSize / 2;
    const cy = cssSize / 2;
    const pxPerM = (cssSize / 2) / PLOT_MAX_M;

    // Background.
    ctx.clearRect(0, 0, cssSize, cssSize);
    ctx.fillStyle = '#0f172a';
    ctx.fillRect(0, 0, cssSize, cssSize);

    // Range rings + labels.
    ctx.strokeStyle = '#1e293b';
    ctx.fillStyle = '#475569';
    ctx.font = '11px ui-sans-serif, system-ui';
    ctx.lineWidth = 1;
    for (let r = RING_STEP_M; r <= PLOT_MAX_M + 1e-3; r += RING_STEP_M) {
      ctx.beginPath();
      ctx.arc(cx, cy, r * pxPerM, 0, Math.PI * 2);
      ctx.stroke();
      ctx.fillText(r.toFixed(0) + ' m', cx + 4, cy - r * pxPerM - 2);
    }

    // Cross-hairs.
    ctx.strokeStyle = '#1e293b';
    ctx.beginPath();
    ctx.moveTo(cx, 0); ctx.lineTo(cx, cssSize);
    ctx.moveTo(0, cy); ctx.lineTo(cssSize, cy);
    ctx.stroke();

    // Heading marker (robot's "front" = up).
    ctx.fillStyle = '#fbbf24';
    ctx.beginPath();
    ctx.moveTo(cx, cy - 10);
    ctx.lineTo(cx - 6, cy + 6);
    ctx.lineTo(cx + 6, cy + 6);
    ctx.closePath();
    ctx.fill();

    // Points.  RPLidar angles are degrees clockwise from front, so
    // theta = (angle - 90) deg gives a "front = up" canvas.
    ctx.fillStyle = '#38bdf8';
    for (const [angDeg, distMm] of scan.points) {
      const distM = distMm / 1000;
      if (distM > PLOT_MAX_M) continue;
      const theta = (angDeg - 90) * Math.PI / 180;
      const x = cx + distM * pxPerM * Math.cos(theta);
      const y = cy + distM * pxPerM * Math.sin(theta);
      ctx.beginPath();
      ctx.arc(x, y, 1.6, 0, Math.PI * 2);
      ctx.fill();
    }

    // Pills.
    scanEl.textContent = scan.id;
    ptsEl.textContent  = scan.n;
    const now = performance.now();
    recent.push(now);
    while (recent.length > 1 && now - recent[0] > 2000) recent.shift();
    const dt = (recent[recent.length - 1] - recent[0]) / 1000;
    hzEl.textContent = dt > 0 ? (recent.length / dt).toFixed(1) : '—';
  }

  function connect() {
    statusEl.textContent = 'connecting…';
    const es = new EventSource('/scan.sse');
    es.onopen = () => { statusEl.textContent = 'live'; };
    es.onmessage = (e) => {
      try { drawScan(JSON.parse(e.data)); }
      catch (err) { console.error('bad scan', err); }
    };
    es.onerror = () => {
      statusEl.textContent = 'reconnecting…';
      // EventSource auto-reconnects; nothing else to do.
    };
  }
  connect();
})();
</script>
</body>
</html>
""".encode('utf-8')


class LidarHandler(http.server.BaseHTTPRequestHandler):
    server_version = 'RobotWebLidar/1.0'

    def log_message(self, fmt, *args):
        sys.stderr.write('[web-lidar] %s - %s\n' % (self.address_string(), fmt % args))

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/' or path == '/index.html':
            self._send_bytes(200, 'text/html; charset=utf-8', INDEX_HTML)
        elif path == '/scan.sse':
            self._stream_sse()
        elif path == '/scan.json':
            # One-shot snapshot — handy for curl / debugging.
            latest = self.server.broker._latest  # type: ignore[attr-defined]
            if latest is None:
                self._send_bytes(503, 'text/plain', b'no scan yet\n')
            else:
                # Strip the "data: " prefix and trailing blank line.
                body = latest[len(b'data: '):-2]
                self._send_bytes(200, 'application/json', body)
        elif path == '/healthz':
            self._send_bytes(200, 'text/plain', b'ok\n')
        else:
            self._send_bytes(404, 'text/plain', b'not found\n')

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

    def _stream_sse(self):
        broker: ScanBroker = self.server.broker  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Connection', 'keep-alive')
        self.send_header('X-Accel-Buffering', 'no')   # disable proxy buffering if any
        self.end_headers()

        q = broker.register()
        try:
            # Server-Sent Events: each message is "data: <payload>\n\n".
            # A periodic comment ":\n\n" keeps the connection alive when
            # the lidar is silent (e.g. unplugged).
            last_keepalive = time.monotonic()
            while True:
                try:
                    frame = q.get(timeout=5.0)
                except queue.Empty:
                    frame = b':\n\n'  # heartbeat
                self.wfile.write(frame)
                self.wfile.flush()
                last_keepalive = time.monotonic()
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            broker.unregister(q)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    p = argparse.ArgumentParser(description='Browser lidar viewer: live polar plot via SSE.')
    p.add_argument('--host', default='0.0.0.0', help='HTTP bind address (default: 0.0.0.0).')
    p.add_argument('--port', type=int, default=8091, help='HTTP port (default: 8091).')
    p.add_argument('--lidar-port', default='/dev/ttyUSB0',
                   help='Serial device for the RPLidar (default: /dev/ttyUSB0).')
    args = p.parse_args()

    broker = ScanBroker()
    lidar = LidarSource(port=args.lidar_port, on_scan=broker.publish)
    if not lidar.available:
        print(f'[web-lidar] lidar unavailable: {lidar.reason}', file=sys.stderr)
        print('[web-lidar] the page will still load and show a "reconnecting" indicator.',
              file=sys.stderr)
    lidar.start()

    server = ThreadedHTTPServer((args.host, args.port), LidarHandler)
    server.broker = broker  # type: ignore[attr-defined]

    print(f'[web-lidar] serving http://{args.host}:{args.port}/  (Ctrl-C to stop)',
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('[web-lidar] shutting down.', file=sys.stderr)
    finally:
        server.server_close()
        lidar.stop()


if __name__ == '__main__':
    main()
