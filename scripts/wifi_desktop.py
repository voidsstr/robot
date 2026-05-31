#!/usr/bin/env python3
"""Pi desktop GUI for WiFi-mode operation.

Single Tk window with three side-by-side panels:

  1. CameraPanel — live IMX519 preview at ~5 fps (flipped 180° because
                   the sensor is mounted upside-down). Includes the
                   Lawn Check / port-switch / Camera Info buttons.
  2. LidarPanel  — top-down polar plot of the latest RPLidar scan.
                   Robot at centre, range rings in metres, nearest hit
                   in each 30° sector labelled. Hidden with --no-lidar.
  3. DrivePanel  — on-screen D-pad + status. Sends UP/DOWN/LEFT/RIGHT/
                   STOP to `bin/robot wifi-server` on 127.0.0.1:8080.
                   The same arrow keys / WASD / Space work as soon as
                   this window has focus (hold to drive, release for STOP).

Hardware reuse: CameraSource and LidarSource come straight from
scripts/ble_server.py — there's only one camera and one lidar attached
to the Pi, so wifi-desktop and ble_server cannot run simultaneously
(scripts/run-wifi-desktop.sh handles that).

Usage:
    python3 scripts/wifi_desktop.py
    python3 scripts/wifi_desktop.py --no-lidar    # camera + controls
    python3 scripts/wifi_desktop.py --no-camera   # lidar + controls
    python3 scripts/wifi_desktop.py --no-flip     # keep image right-side-up

Run alongside `bin/robot wifi-server`. The wrapper script
scripts/run-wifi-desktop.sh starts both for you.
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from ble_server import CameraSource, LidarSource  # type: ignore
except Exception as e:
    sys.exit(f'Could not import CameraSource/LidarSource from ble_server.py: {e}\n'
             'Make sure scripts/ble_server.py is present and unmodified.')

# Reuse the proven persistent-socket command client from web_drive.py so
# both UIs talk to wifi-server the same way (one connection, serialised
# writes, single reconnect on transport error).
try:
    from web_drive import CommandClient  # type: ignore
except Exception as e:
    sys.exit(f'Could not import CommandClient from web_drive.py: {e}')

try:
    from lawn_camera import encode_for_api as _lawn_encode  # type: ignore
    from lawn_camera import assess_lawn as _lawn_assess    # type: ignore
    _LAWN_AVAILABLE = True
    _LAWN_IMPORT_ERROR = ''
except Exception as e:
    _lawn_encode = None
    _lawn_assess = None
    _LAWN_AVAILABLE = False
    _LAWN_IMPORT_ERROR = str(e)


LIDAR_MIN_M = 0.10
LIDAR_MAX_M = 12.0

BG       = '#0f172a'
BG_DARK  = '#020617'
FG       = '#e2e8f0'
FG_DIM   = '#94a3b8'
ACCENT   = '#22c55e'
ACTIVE   = '#2563eb'


# --------------------------------------------------------------------------- #
# Logger + BLE log tail
# --------------------------------------------------------------------------- #
class Logger:
    """Thread-safe append-only log view. Entries are queued from any thread
    and flushed onto the Tk Text widget via after_idle. Old lines are trimmed
    once we exceed MAX_LINES."""

    MAX_LINES = 5000
    TAG_COLORS = {
        'GUI':       '#60a5fa',  # commands the GUI's drive controls send
        'WIFI':      '#22d3ee',  # wifi-server replies (= byte to Arduino)
        'BLE':       '#86efac',  # commands arriving over BLE
        'BLE_STATE': '#fbbf24',  # BLE connect / disconnect / pair events
        'ERROR':     '#f87171',
        'INFO':      '#94a3b8',
    }

    def __init__(self, parent: tk.Widget):
        self.frame = tk.Frame(parent, bg=BG)

        toolbar = tk.Frame(self.frame, bg=BG)
        toolbar.pack(side='top', fill='x', padx=8, pady=(8, 4))

        tk.Label(toolbar, text='Real-time log: GUI commands, wifi-server replies, BLE events.',
                 bg=BG, fg=FG_DIM, font=('Helvetica', 10), anchor='w'
                 ).pack(side='left', fill='x', expand=True)

        self._autoscroll_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text='Auto-scroll', variable=self._autoscroll_var,
                       bg=BG, fg=FG_DIM, selectcolor=BG_DARK,
                       activebackground=BG, activeforeground=FG,
                       font=('Helvetica', 10), takefocus=0
                       ).pack(side='left', padx=(0, 8))

        tk.Button(toolbar, text='Clear', command=self.clear,
                  bg='#1e293b', fg=FG, activebackground='#334155',
                  font=('Helvetica', 10), bd=0, padx=10, pady=4, takefocus=0
                  ).pack(side='right')

        text_frame = tk.Frame(self.frame, bg=BG)
        text_frame.pack(side='top', fill='both', expand=True, padx=8, pady=(0, 8))
        self._text = tk.Text(text_frame, bg=BG_DARK, fg=FG, bd=0,
                             font=('Monospace', 10), wrap='none',
                             state='disabled', highlightthickness=0,
                             insertbackground=FG)
        self._text.pack(side='left', fill='both', expand=True)
        scroll = tk.Scrollbar(text_frame, command=self._text.yview)
        scroll.pack(side='right', fill='y')
        self._text.config(yscrollcommand=scroll.set)

        for tag, color in self.TAG_COLORS.items():
            self._text.tag_config(tag, foreground=color)

        self._lock = threading.Lock()
        self._pending: list[tuple[str, str]] = []
        self._flush_scheduled = False

    def add(self, tag: str, msg: str):
        ts = time.strftime('%H:%M:%S')
        line = f'{ts}  [{tag:<9}]  {msg}\n'
        if tag not in self.TAG_COLORS:
            tag = 'INFO'
        with self._lock:
            self._pending.append((tag, line))
            need_flush = not self._flush_scheduled
            if need_flush:
                self._flush_scheduled = True
        if need_flush:
            try:
                self._text.after_idle(self._flush)
            except RuntimeError:
                pass  # Tk shutting down

    def _flush(self):
        with self._lock:
            items = self._pending
            self._pending = []
            self._flush_scheduled = False
        if not items:
            return
        self._text.config(state='normal')
        for tag, line in items:
            self._text.insert('end', line, tag)
        total_lines = int(self._text.index('end-1c').split('.')[0])
        if total_lines > self.MAX_LINES:
            self._text.delete('1.0', f'{total_lines - self.MAX_LINES + 1}.0')
        self._text.config(state='disabled')
        if self._autoscroll_var.get():
            self._text.see('end')

    def clear(self):
        self._text.config(state='normal')
        self._text.delete('1.0', 'end')
        self._text.config(state='disabled')


class BleRelay:
    """TCP listener that accepts ONE client (the BLE bridge) and forwards
    its newline-terminated CMD lines through the GUI's CommandClient.

    Why: wifi-server is single-client. The GUI's CommandClient already
    holds that slot, so the BLE bridge can't connect to wifi-server
    directly. Instead, the BLE bridge points its --forward-tcp at us
    (this relay), and we funnel its commands into the GUI's existing
    connection. Bonus: every BLE command lands in the Logger in real
    time without round-tripping through the log file."""

    def __init__(self, host: str, port: int, client: CommandClient,
                 logger: 'Logger', on_command=None, on_mission=None):
        self._host = host
        self._port = port
        self._client = client
        self._logger = logger
        self._on_command = on_command or (lambda _cmd, _reply: None)
        # on_mission(verb, arg) -> (ok: bool, reply: str). Called for
        # MISSION / MISSION-ABORT / MISSION-STATUS commands from the
        # BLE bridge so the phone can start/abort/query missions.
        self._on_mission = on_mission or (lambda _v, _a: (False, 'ERR: mission handler not wired'))
        self._stop = threading.Event()
        self._listen_sock: socket.socket | None = None
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='wifi-desktop-ble-relay')

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._listen_sock:
                self._listen_sock.close()
        except Exception:
            pass

    def _loop(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self._host, self._port))
            s.listen(1)
            s.settimeout(0.5)
            self._listen_sock = s
        except Exception as e:
            self._logger.add('ERROR',
                             f'BLE relay bind {self._host}:{self._port} failed: {e}')
            return
        self._logger.add('INFO',
                         f'BLE relay listening on {self._host}:{self._port} '
                         '(BLE bridge -> GUI -> wifi-server)')
        while not self._stop.is_set():
            try:
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except Exception:
                if self._stop.is_set():
                    break
                continue
            conn.settimeout(2.0)
            self._logger.add('INFO',
                             f'BLE bridge attached to relay: {addr[0]}:{addr[1]}')
            try:
                conn.sendall(b'OK: relay ready\n')
                self._serve(conn)
            except Exception as e:
                self._logger.add('ERROR', f'BLE relay client error: {e}')
            finally:
                try: conn.close()
                except Exception: pass
                self._logger.add('INFO', 'BLE bridge detached from relay.')

    def _serve(self, conn: socket.socket):
        buf = b''
        while not self._stop.is_set():
            try:
                data = conn.recv(256)
            except socket.timeout:
                continue
            if not data:
                print('[relay] BLE bridge closed the connection', file=sys.stderr)
                return
            buf += data
            while b'\n' in buf:
                line, _, buf = buf.partition(b'\n')
                cmd = line.decode('ascii', errors='replace').strip()
                if not cmd:
                    continue
                cmd_upper = cmd.upper()

                # Mission verbs: handled in-GUI, never reach wifi-server.
                if cmd_upper.startswith('MISSION'):
                    parts = cmd.split(None, 1)
                    verb = parts[0].upper()
                    arg = parts[1].strip() if len(parts) > 1 else ''
                    try:
                        ok, reply = self._on_mission(verb, arg)
                    except Exception as e:
                        ok, reply = False, f'ERR: mission handler: {e}'
                    print(f'[relay] mission {verb} {arg!r} -> '
                          f'{"OK" if ok else "FAIL"}: {reply}', file=sys.stderr)
                    reply_bytes = ((reply or 'OK') + '\n').encode('ascii')
                    try:
                        conn.sendall(reply_bytes)
                    except Exception:
                        return
                    self._logger.add('BLE' if ok else 'ERROR',
                                     f'phone → {verb} {arg} → {reply}')
                    continue

                cmd = cmd_upper
                ok, reply = self._client.send_cmd(cmd)
                print(f'[relay] phone {cmd} -> wifi-server {"OK" if ok else "FAIL"}: {reply}',
                      file=sys.stderr)
                reply_bytes = ((reply or 'OK') + '\n').encode('ascii')
                try:
                    conn.sendall(reply_bytes)
                except Exception:
                    return
                if ok:
                    self._logger.add('BLE', f'phone → {cmd}')
                    self._logger.add('WIFI', f'  forwarded: {cmd} → {reply}')
                else:
                    self._logger.add('ERROR', f'phone → {cmd} (wifi-server: {reply})')
                self._on_command(cmd, reply)


class JpegFramePublisher:
    """TCP publisher of JPEG video frames for any local subscriber to read.

    Wire format: repeating [4-byte big-endian length][JPEG bytes].
    Each accepted client gets its own thread that waits on a condition
    variable, grabs the latest frame, and sends it. Slow clients miss
    intermediate frames (latest-wins) but never block the publisher.

    The BLE bridge connects here via --frame-source so the camera can be
    owned by the GUI and the phone still sees video — without the BLE
    bridge fighting picamera2 for /dev/video0."""

    def __init__(self, host: str, port: int, logger: 'Logger'):
        self._host = host
        self._port = port
        self._logger = logger
        self._latest_jpeg: bytes | None = None
        self._latest_id = 0
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._listen_sock: socket.socket | None = None
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True,
                                               name='wifi-desktop-jpeg-pub-accept')

    def start(self):
        self._accept_thread.start()

    def stop(self):
        self._stop.set()
        try:
            if self._listen_sock:
                self._listen_sock.close()
        except Exception:
            pass
        with self._cond:
            self._cond.notify_all()

    def publish(self, jpeg_bytes: bytes):
        with self._cond:
            self._latest_jpeg = jpeg_bytes
            self._latest_id += 1
            self._cond.notify_all()

    def _accept_loop(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((self._host, self._port))
            s.listen(2)
            s.settimeout(0.5)
            self._listen_sock = s
        except Exception as e:
            self._logger.add('ERROR',
                             f'JPEG publisher bind {self._host}:{self._port} failed: {e}')
            return
        self._logger.add('INFO',
                         f'JPEG frame publisher listening on {self._host}:{self._port}')
        while not self._stop.is_set():
            try:
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except Exception:
                if self._stop.is_set():
                    break
                continue
            self._logger.add('INFO', f'Frame subscriber connected: {addr[0]}:{addr[1]}')
            t = threading.Thread(target=self._serve_client, args=(conn, addr),
                                 daemon=True, name='jpeg-pub-client')
            t.start()

    def _serve_client(self, conn: socket.socket, addr):
        import struct
        last_id = -1
        try:
            conn.settimeout(5.0)
            while not self._stop.is_set():
                with self._cond:
                    while not self._stop.is_set() and self._latest_id == last_id:
                        self._cond.wait(timeout=0.5)
                    if self._stop.is_set():
                        break
                    jpeg = self._latest_jpeg
                    last_id = self._latest_id
                if not jpeg:
                    continue
                msg = struct.pack('>I', len(jpeg)) + jpeg
                try:
                    conn.sendall(msg)
                except Exception:
                    break
        finally:
            try: conn.close()
            except Exception: pass
            self._logger.add('INFO', f'Frame subscriber disconnected: {addr[0]}:{addr[1]}')


class BleLogTail:
    """Background thread that tails a file written by ble_server.py, parses
    its '[ble] …' lines, routes events into the Logger, and notifies a
    callback so the BLE status panel can update."""

    def __init__(self, path: str, logger: Logger, on_state_change):
        self._path = path
        self._logger = logger
        self._on_state_change = on_state_change   # (state, last_line) -> None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name='wifi-desktop-ble-tail')

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        # Wait for the file to appear (the launcher creates it at startup,
        # but our thread may race with it).
        deadline = time.monotonic() + 30
        while not self._stop.is_set() and not os.path.exists(self._path):
            if time.monotonic() > deadline:
                self._logger.add('ERROR',
                                 f'BLE log not found at {self._path} after 30s; '
                                 'is ble_server.py running?')
                return
            time.sleep(0.5)
        try:
            f = open(self._path, 'r', errors='replace')
        except Exception as e:
            self._logger.add('ERROR', f'Could not open BLE log {self._path}: {e}')
            return
        try:
            inode = os.fstat(f.fileno()).st_ino
        except Exception:
            inode = None
        # Read from the beginning so we catch the startup banner ("advertising
        # the Nordic UART Service…") on a fresh launcher start. The launcher
        # truncates the log every run, so volume stays bounded.
        while not self._stop.is_set():
            line = f.readline()
            if line:
                self._handle(line.rstrip('\n'))
                continue
            time.sleep(0.1)
            try:
                st = os.stat(self._path)
                if inode is not None and st.st_ino != inode:
                    try: f.close()
                    except Exception: pass
                    f = open(self._path, 'r', errors='replace')
                    inode = os.fstat(f.fileno()).st_ino
            except FileNotFoundError:
                pass
        try: f.close()
        except Exception: pass

    def _handle(self, line: str):
        if not line.strip():
            return
        # Strip ANSI escape sequences that wifi-server / TUI bits sometimes
        # emit in the same log file (we tee both subprocesses there).
        if 'device CONNECTED' in line:
            self._on_state_change('connected', line)
            self._logger.add('BLE_STATE', line)
        elif 'device DISCONNECTED' in line:
            self._on_state_change('disconnected', line)
            self._logger.add('BLE_STATE', line)
        elif 'advertising the Nordic UART Service' in line:
            self._on_state_change('advertising', line)
            self._logger.add('BLE_STATE', line)
        elif 'Auto-accept pairing agent registered' in line:
            self._logger.add('BLE_STATE', line)
        elif line.startswith('[ble] ') and ' -> ' in line:
            # E.g. "[ble] UP -> U"  → BLE command was received
            self._logger.add('BLE', line[6:])
        elif line.startswith('[ble]'):
            self._logger.add('BLE', line[6:].lstrip())
        elif line.startswith('FATAL') or 'failed' in line.lower() or 'error' in line.lower():
            self._logger.add('ERROR', line)
        else:
            self._logger.add('INFO', line)


# --------------------------------------------------------------------------- #
# Camera panel
# --------------------------------------------------------------------------- #
class CameraPanel:
    """Live camera preview + camera-related buttons, packed into a parent frame.

    Tk widgets are not thread-safe, so a background thread pulls frames
    from picamera2 into a tiny queue (size 1, latest-wins) and the Tk
    main thread reads from it via .after(). The camera is physically
    upside-down on this robot, so frames get a 180° rotation before
    being shown or sent to the lawn-check API."""

    def __init__(self, root: tk.Tk, parent: tk.Widget, camera, fps: int = 5,
                 flip: bool = True, frame_publisher: 'JpegFramePublisher | None' = None,
                 publish_size=(320, 240), publish_quality: int = 60):
        self._root = root
        self._camera = camera
        self._fps = max(1, min(15, int(fps)))
        self._flip = flip
        self._frame_publisher = frame_publisher
        self._publish_size = publish_size
        self._publish_quality = publish_quality
        self._stop = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._lawn_busy = False

        # pack_propagate(False) so the camera image (which we resize to
        # fit the canvas) can't push the panel — and the whole window —
        # to grow frame-by-frame.
        self.frame = tk.Frame(parent, bg=BG, width=700)
        self.frame.pack_propagate(False)

        self._label_status = tk.Label(
            self.frame, text='', fg=FG_DIM, bg=BG,
            font=('Helvetica', 11), anchor='w', padx=10, pady=6,
        )
        self._label_status.pack(side='top', fill='x')

        self._canvas_image = tk.Canvas(self.frame, bg=BG_DARK,
                                       highlightthickness=0)
        self._canvas_image.pack(side='top', fill='both', expand=True,
                                padx=8, pady=(0, 8))
        self._canvas_image_id = None

        ctrl = tk.Frame(self.frame, bg=BG)
        ctrl.pack(side='top', fill='x', padx=8, pady=(0, 8))
        self._btn_lawn = tk.Button(
            ctrl, text='🌱 Lawn Check', command=self._on_lawn_check,
            bg=ACCENT, fg=BG, activebackground='#16a34a',
            font=('Helvetica', 11, 'bold'), bd=0, padx=12, pady=6,
        )
        self._btn_lawn.pack(side='left', padx=(0, 6))
        self._btn_cam0 = tk.Button(
            ctrl, text='Port 0', command=lambda: self._on_switch_port(0),
            bg='#1e293b', fg=FG, activebackground='#334155',
            font=('Helvetica', 10), bd=0, padx=10, pady=6,
        )
        self._btn_cam0.pack(side='left', padx=2)
        self._btn_cam1 = tk.Button(
            ctrl, text='Port 1', command=lambda: self._on_switch_port(1),
            bg='#1e293b', fg=FG, activebackground='#334155',
            font=('Helvetica', 10), bd=0, padx=10, pady=6,
        )
        self._btn_cam1.pack(side='left', padx=2)
        self._btn_caminfo = tk.Button(
            ctrl, text='Camera Info', command=self._on_camera_info,
            bg='#1e293b', fg=FG, activebackground='#334155',
            font=('Helvetica', 10), bd=0, padx=10, pady=6,
        )
        self._btn_caminfo.pack(side='left', padx=2)

        if not _LAWN_AVAILABLE:
            self._btn_lawn.config(state='disabled', bg='#334155', fg='#64748b')

        self._photo = None

        try:
            from PIL import Image, ImageTk  # type: ignore
            self._Image = Image
            self._ImageTk = ImageTk
        except ImportError:
            self._set_status('PIL/ImageTk not installed (sudo apt-get install python3-pil.imagetk).')
            self._Image = None
            self._ImageTk = None

        if camera is None or not camera.available:
            self._set_status('Camera not available — running with no live preview.')
            return

        if self._Image is None or self._ImageTk is None:
            return

        flip_note = '' if flip else ' (no flip)'
        self._set_status(f'Camera ready ({self._fps} fps){flip_note}.')
        self._thread = threading.Thread(target=self._capture_loop, daemon=True,
                                        name='wifi-desktop-camera')
        self._thread.start()
        self.frame.after(50, self._drain)

    def _set_status(self, txt):
        self._label_status.config(text=txt)

    def _capture_loop(self):
        period = 1.0 / self._fps
        while not self._stop.is_set():
            t0 = time.monotonic()
            if self._camera is None:
                break
            rgb = self._camera.capture_video_rgb()
            if rgb is not None:
                try:
                    self._queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._queue.put_nowait(rgb)
                except queue.Full:
                    pass
                # Also encode a smaller JPEG for the network publisher
                # (BLE bridge subscribes here so phones get video).
                if self._frame_publisher is not None and self._Image is not None:
                    try:
                        self._frame_publisher.publish(self._encode_publish_jpeg(rgb))
                    except Exception:
                        pass
            dt = time.monotonic() - t0
            if dt < period:
                time.sleep(period - dt)

    def _encode_publish_jpeg(self, rgb):
        import io
        img = self._Image.fromarray(rgb)
        if self._flip:
            img = img.transpose(self._Image.ROTATE_180)
        # thumbnail() preserves aspect ratio and only downscales — never
        # upscales — so a 320x240 publish target always stays within the
        # bandwidth budget the BLE link can handle.
        img.thumbnail(self._publish_size, self._Image.BILINEAR)
        buf = io.BytesIO()
        img.save(buf, format='JPEG', quality=self._publish_quality)
        return buf.getvalue()

    def _drain(self):
        if self._stop.is_set():
            return
        try:
            rgb = self._queue.get_nowait()
        except queue.Empty:
            rgb = None
        if rgb is not None and self._Image is not None and self._ImageTk is not None:
            try:
                img = self._Image.fromarray(rgb)
                if self._flip:
                    img = img.transpose(self._Image.ROTATE_180)
                cw = max(160, self._canvas_image.winfo_width())
                ch = max(120, self._canvas_image.winfo_height())
                src_w, src_h = img.size
                scale = min(cw / src_w, ch / src_h)
                tw, th = max(1, int(src_w * scale)), max(1, int(src_h * scale))
                img = img.resize((tw, th), self._Image.BILINEAR)
                self._photo = self._ImageTk.PhotoImage(img)
                if self._canvas_image_id is None:
                    self._canvas_image_id = self._canvas_image.create_image(
                        cw // 2, ch // 2, image=self._photo, anchor='center')
                else:
                    self._canvas_image.coords(self._canvas_image_id,
                                              cw // 2, ch // 2)
                    self._canvas_image.itemconfig(self._canvas_image_id,
                                                  image=self._photo)
            except Exception as e:
                self._set_status(f'Display error: {e}')
        self.frame.after(50, self._drain)

    # ----- Camera-port + lawn-check controls --------------------------------

    def _on_switch_port(self, port):
        if self._camera is None:
            self._set_status(f'Cannot switch to port {port}: --no-camera at startup.')
            return
        print(f'[desktop] requesting camera port switch to {port}…', file=sys.stderr)
        self._set_status(f'Switching to port {port}…')
        try:
            self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            actual = self._camera.set_camera_num(port)
            self._set_status(f'Camera now on port {actual}.')
            print(f'[desktop] camera switched to port {actual}.', file=sys.stderr)
        except Exception as e:
            self._set_status(f'Port {port} failed: {e}')
            print(f'[desktop] port switch FAILED: {e}', file=sys.stderr)

    def _on_camera_info(self):
        if self._camera is None:
            self._set_status('No camera available (--no-camera).')
            return
        cams = self._camera.cameras
        st = self._camera.state
        active = st['camera_num'] if st['camera_num'] is not None else 'default'
        print('[desktop] =========== CAMERA INFO ===========', file=sys.stderr)
        if not cams:
            print('[desktop] No cameras detected by libcamera.', file=sys.stderr)
            print(f'[desktop] last error: {self._camera.last_error}', file=sys.stderr)
        else:
            print(f'[desktop] active port: {active}', file=sys.stderr)
            for i, info in enumerate(cams):
                print(f'  [{i}] {info}', file=sys.stderr)
        print('[desktop] ===================================', file=sys.stderr)
        if not cams:
            self._set_status(f'CAMINFO: none detected ({self._camera.last_error or "?"}).')
        else:
            summary = ', '.join(f'#{c.get("Num", "?")}:{c.get("Model", "?")}@loc{c.get("Location", "?")}'
                                 for c in cams)
            self._set_status(f'CAMINFO: {len(cams)} cam(s), active=port {active}. {summary}')

    # ----- snapshot helpers (used by Routes / Mission tabs) ----------------
    def capture_jpeg_now(self) -> bytes:
        """Synchronous JPEG capture from the live camera, flipped if needed.
        Used by waypoint capture + by the MissionRunner per-step frame."""
        if self._camera is None:
            return b''
        try:
            jpeg = self._camera.capture_video_jpeg()
        except Exception:
            return b''
        if not jpeg:
            return b''
        if not self._flip or self._Image is None:
            return jpeg
        try:
            import io
            img = self._Image.open(io.BytesIO(jpeg)).transpose(self._Image.ROTATE_180)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            return buf.getvalue()
        except Exception:
            return jpeg

    def trigger_lawn_check(self):
        """Public hook so the MissionRunner can fire the same lawn-check
        path the on-screen button uses (Claude assessment + popup)."""
        self._root.after_idle(self._on_lawn_check)

    def _on_lawn_check(self):
        if self._lawn_busy:
            return
        if not _LAWN_AVAILABLE:
            self._set_status(f'Lawn check unavailable: {_LAWN_IMPORT_ERROR}')
            return
        if self._camera is None or not self._camera.available:
            self._set_status('Lawn check: camera not available.')
            return
        if not os.environ.get('ANTHROPIC_API_KEY'):
            self._set_status('Lawn check: set ANTHROPIC_API_KEY before launching the desktop.')
            print('[desktop] LAWN CHECK aborted: ANTHROPIC_API_KEY is not set.', file=sys.stderr)
            return

        self._lawn_busy = True
        self._btn_lawn.config(state='disabled', text='🌱 Working…', bg='#334155', fg=FG_DIM)
        self._set_status('Lawn check: capturing photo + asking Claude…')
        print('[desktop] LAWN CHECK starting…', file=sys.stderr)

        flip = self._flip
        Image = self._Image

        def _work():
            try:
                jpeg = self._camera.capture_photo_jpeg()
                if not jpeg:
                    raise RuntimeError('Photo capture returned no bytes.')
                # Always re-encode if PIL is available so we can flip AND
                # add a touch of saturation + contrast — Pi/IMX519 stills
                # come out a bit washed-out, and the muted greens hurt
                # the model's read of lawn health.
                if Image is not None:
                    import io
                    from PIL import ImageEnhance  # type: ignore
                    img = Image.open(io.BytesIO(jpeg))
                    if flip:
                        img = img.transpose(Image.ROTATE_180)
                    img = ImageEnhance.Color(img).enhance(1.35)     # +35% saturation
                    img = ImageEnhance.Contrast(img).enhance(1.15)  # +15% contrast
                    img = ImageEnhance.Sharpness(img).enhance(1.1)  # mild edge crispness
                    buf = io.BytesIO()
                    img.save(buf, format='JPEG', quality=95)
                    jpeg = buf.getvalue()
                import tempfile
                fd, path = tempfile.mkstemp(prefix='lawncam_desktop_', suffix='.jpg')
                try:
                    os.write(fd, jpeg)
                    os.close(fd)
                    b64, media_type = _lawn_encode(path)
                    result = _lawn_assess(b64, media_type)
                finally:
                    try: os.unlink(path)
                    except Exception: pass
                self._root.after_idle(lambda: self._on_lawn_done(jpeg, result))
            except Exception as e:
                err = str(e)
                print(f'[desktop] LAWN CHECK failed: {err}', file=sys.stderr)
                self._root.after_idle(lambda: self._on_lawn_failed(err))

        threading.Thread(target=_work, daemon=True, name='wifi-desktop-lawn').start()

    def _on_lawn_done(self, jpeg_bytes, result):
        self._lawn_busy = False
        self._btn_lawn.config(state='normal', text='🌱 Lawn Check',
                              bg=ACCENT, fg=BG)
        self._set_status('Lawn check complete — see popup.')
        print('[desktop] =========== LAWN CHECK RESULT ===========', file=sys.stderr)
        try:
            import json
            print(json.dumps(result, indent=2), file=sys.stderr)
        except Exception:
            print(repr(result), file=sys.stderr)
        print('[desktop] ==========================================', file=sys.stderr)
        LawnResultPopup(self._root, jpeg_bytes, result)

    def _on_lawn_failed(self, msg):
        self._lawn_busy = False
        self._btn_lawn.config(state='normal', text='🌱 Lawn Check',
                              bg=ACCENT, fg=BG)
        self._set_status(f'Lawn check failed: {msg}')

    def close(self):
        self._stop.set()


class LawnResultPopup:
    """Toplevel that shows the JPEG + grass-health verdict. Esc closes."""

    def __init__(self, root: tk.Tk, jpeg_bytes: bytes, result: dict):
        self.top = tk.Toplevel(root)
        self.top.title('Lawn Check Result')
        self.top.configure(bg=BG)
        self.top.geometry('560x720')
        self.top.bind('<Escape>', lambda _e: self.top.destroy())

        score = result.get('health_score')
        present = bool(result.get('lawn_present'))
        if not present:
            bucket_label = 'No lawn detected'
            bucket_color = FG_DIM
            score_text = 'n/a'
        else:
            try:
                s = int(score) if score is not None else 0
            except Exception:
                s = 0
            if s <= 30:    bucket_label, bucket_color = 'Poor', '#ef4444'
            elif s <= 75:  bucket_label, bucket_color = 'Fair', '#eab308'
            else:          bucket_label, bucket_color = 'Healthy', ACCENT
            score_text = f'{max(0, min(100, s))} / 100'

        hdr = tk.Frame(self.top, bg=BG)
        hdr.pack(side='top', fill='x', padx=14, pady=(14, 6))
        tk.Label(hdr, text='LAWN HEALTH', bg=BG, fg=FG_DIM,
                 font=('Helvetica', 10, 'bold')).pack(side='left')
        tk.Label(hdr, text=score_text, bg=BG, fg=bucket_color,
                 font=('Helvetica', 22, 'bold')).pack(side='right')

        tk.Label(self.top, text=bucket_label, bg=BG, fg=bucket_color,
                 font=('Helvetica', 12, 'bold')).pack(side='top', anchor='w', padx=14)

        try:
            from PIL import Image, ImageTk  # type: ignore
            import io
            img = Image.open(io.BytesIO(jpeg_bytes))
            w, h = img.size
            scale = min(520.0 / w, 360.0 / h, 1.0)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
            self._photo = ImageTk.PhotoImage(img)
            tk.Label(self.top, image=self._photo, bg=BG_DARK,
                     borderwidth=0).pack(side='top', padx=14, pady=10)
        except Exception as e:
            tk.Label(self.top, text=f'(image preview failed: {e})',
                     bg=BG, fg=FG_DIM).pack(side='top', padx=14, pady=10)

        body = tk.Frame(self.top, bg=BG)
        body.pack(side='top', fill='both', expand=True, padx=14, pady=(0, 8))

        summary = (result.get('summary') or '').strip() or '(no summary returned)'
        tk.Label(body, text='Summary', bg=BG, fg=FG_DIM,
                 font=('Helvetica', 10, 'bold'), anchor='w').pack(fill='x')
        tk.Message(body, text=summary, width=520, bg=BG, fg=FG,
                   font=('Helvetica', 11)).pack(fill='x', pady=(0, 8))

        issues = result.get('issues') or []
        if issues:
            tk.Label(body, text='Issues', bg=BG, fg=FG_DIM,
                     font=('Helvetica', 10, 'bold'), anchor='w').pack(fill='x')
            tk.Message(body, text='• ' + '\n• '.join(issues), width=520,
                       bg=BG, fg='#fcd34d',
                       font=('Helvetica', 10)).pack(fill='x', pady=(0, 8))

        recs = result.get('recommendations') or []
        if recs:
            tk.Label(body, text='Recommendations  —  what to do, and when',
                     bg=BG, fg=FG_DIM,
                     font=('Helvetica', 10, 'bold'), anchor='w').pack(fill='x')
            # Sort high-priority items to the top so the operator sees the
            # urgent stuff first.
            PRI_ORDER = {'high': 0, 'medium': 1, 'low': 2, '': 3}
            PRI_COLOR = {'high': '#f87171', 'medium': '#fbbf24', 'low': '#86efac'}

            def _key(r):
                if isinstance(r, dict):
                    return PRI_ORDER.get((r.get('priority') or '').lower(), 3)
                return 3
            for r in sorted(recs, key=_key):
                if isinstance(r, dict):
                    pri = (r.get('priority') or '').lower()
                    action = (r.get('action') or '').strip()
                    when = (r.get('when') or '').strip()
                    color = PRI_COLOR.get(pri, '#86efac')
                    pri_tag = f'[{pri.upper()}] ' if pri else ''
                    row = tk.Frame(body, bg=BG)
                    row.pack(fill='x', pady=(0, 4))
                    tk.Label(row, text='•', bg=BG, fg=color,
                             font=('Helvetica', 11, 'bold')
                             ).pack(side='left', anchor='nw', padx=(0, 6))
                    text_col = tk.Frame(row, bg=BG)
                    text_col.pack(side='left', fill='x', expand=True)
                    tk.Message(text_col, text=f'{pri_tag}{action}', width=480,
                               bg=BG, fg=color, anchor='w',
                               font=('Helvetica', 10, 'bold')).pack(fill='x', anchor='w')
                    if when:
                        tk.Message(text_col, text=f'When: {when}', width=480,
                                   bg=BG, fg=FG, anchor='w',
                                   font=('Helvetica', 10)).pack(fill='x', anchor='w')
                else:
                    # Legacy plain-string shape.
                    tk.Message(body, text=f'• {r}', width=520,
                               bg=BG, fg='#86efac',
                               font=('Helvetica', 10)).pack(fill='x', pady=(0, 4))

        meta_bits = []
        if result.get('_model'): meta_bits.append(f'model={result["_model"]}')
        if result.get('confidence') is not None:
            try: meta_bits.append(f'conf={float(result["confidence"]) * 100:.0f}%')
            except Exception: pass
        usage = result.get('_usage') or {}
        if usage:
            meta_bits.append(f'tokens={usage.get("input_tokens", "?")}/{usage.get("output_tokens", "?")}')
        if result.get('health_status'):
            meta_bits.append(f'status={result["health_status"]}')
        if meta_bits:
            tk.Label(self.top, text='  •  '.join(meta_bits),
                     bg=BG, fg='#64748b',
                     font=('Helvetica', 9), anchor='w').pack(side='bottom', fill='x',
                                                              padx=14, pady=(0, 12))

        tk.Button(self.top, text='Close', command=self.top.destroy,
                  bg='#1e293b', fg=FG, activebackground='#334155',
                  font=('Helvetica', 10), bd=0, padx=14, pady=6).pack(
            side='bottom', anchor='e', padx=14, pady=4)


# --------------------------------------------------------------------------- #
# Lidar panel
# --------------------------------------------------------------------------- #
class LidarPanel:
    """Top-down polar plot of the RPLidar scan, packed into a parent frame."""

    RING_COLOR  = '#1e293b'
    AXIS_COLOR  = '#334155'
    POINT_COLOR = '#38bdf8'
    NEAR_COLOR  = '#fbbf24'
    LABEL_COLOR = FG
    ROBOT_COLOR = ACCENT
    SECTOR_DEG  = 30
    REDRAW_MS   = 200

    def __init__(self, root: tk.Tk, parent: tk.Widget, lidar):
        self._root = root
        self._lidar = lidar
        self._latest_points: list[tuple[float, float]] = []
        self._scan_lock = threading.Lock()
        self._max_range_m = 5.0

        self.frame = tk.Frame(parent, bg=BG, width=560)
        self.frame.pack_propagate(False)

        header = tk.Frame(self.frame, bg=BG)
        header.pack(side='top', fill='x')
        self._status = tk.Label(
            header, text='', fg=FG_DIM, bg=BG,
            font=('Helvetica', 10), anchor='w', padx=10, pady=4,
        )
        self._status.pack(side='left', fill='x', expand=True)
        self._retry_btn = tk.Button(
            header, text='Retry', command=self._on_retry,
            bg='#1e293b', fg=FG, activebackground='#334155',
            font=('Helvetica', 9), bd=0, padx=8, pady=2, takefocus=0,
        )
        self._retry_btn.pack(side='right', padx=(4, 6), pady=2)

        self._canvas = tk.Canvas(self.frame, bg=BG, highlightthickness=0)
        self._canvas.pack(side='top', fill='both', expand=True, padx=8, pady=(0, 8))

        if lidar is not None and lidar.available:
            lidar._on_scan = self._on_scan   # noqa: SLF001
            lidar.start()
            self._status.config(text='Lidar starting…')
        else:
            note = lidar.reason if lidar is not None else 'disabled'
            self._status.config(text=f'Lidar: {note}')

        self._last_scan_at = 0.0
        self.frame.after(self.REDRAW_MS, self._redraw)
        self.frame.after(2000, self._health_check)

    def _on_retry(self):
        if self._lidar is None:
            return
        self._status.config(text='Lidar: stopping…')
        try: self._lidar.stop()
        except Exception: pass
        self._status.config(text='Lidar: re-probing…')
        if not self._lidar.reprobe(quiet=False):
            self._status.config(text=f'Lidar: {self._lidar.reason}')
            return
        self._lidar._on_scan = self._on_scan   # noqa: SLF001
        self._lidar.start()
        self._last_scan_at = time.monotonic()
        self._status.config(text='Lidar: restarting…')

    def _health_check(self):
        """Every 3 s: if we claimed available but no scans in the last 5 s,
        nudge the lidar to restart. Covers the 'iter_scans died silently'
        case (e.g. 'Incorrect descriptor starting bytes' on a stale A1)."""
        try:
            if self._lidar is not None and self._lidar.available:
                now = time.monotonic()
                quiet = now - max(self._last_scan_at, 0)
                if self._last_scan_at == 0:
                    self._status.config(
                        text='Lidar: waiting for first scan… (Retry if stuck)')
                if quiet > 5.0 and self._lidar._thread is None:  # noqa: SLF001
                    self._status.config(text='Lidar: scan thread died — auto-restarting…')
                    try: self._lidar.start()
                    except Exception as e:
                        self._status.config(text=f'Lidar restart failed: {e}')
            elif self._lidar is not None and not self._lidar.available:
                self._status.config(text=f'Lidar: {self._lidar.reason} — click Retry')
        finally:
            self.frame.after(3000, self._health_check)

    def _on_scan(self, points):
        usable = []
        for ang, dist_mm in points:
            d_m = dist_mm / 1000.0
            if d_m < LIDAR_MIN_M or d_m > LIDAR_MAX_M:
                continue
            usable.append((float(ang) % 360.0, d_m))
        with self._scan_lock:
            self._latest_points = usable
        self._last_scan_at = time.monotonic()
        try:
            cur_text = self._status.cget('text')
            if cur_text.startswith('Lidar:') and 'running' not in cur_text:
                self._root.after_idle(lambda: self._status.config(
                    text=f'Lidar running — {len(usable)} returns/scan'))
        except Exception:
            pass

    def latest_points(self):
        """Snapshot of the most recent scan as a list of (angle°, dist_m).
        Used by waypoint capture + the MissionRunner safety override."""
        with self._scan_lock:
            return list(self._latest_points)
        self._last_scan_at = time.monotonic()
        if self._status.cget('text').startswith('Lidar:') and 'running' not in self._status.cget('text'):
            try:
                self._root.after_idle(lambda: self._status.config(
                    text=f'Lidar running — {len(usable)} returns/scan'))
            except Exception:
                pass

    def _redraw(self):
        c = self._canvas
        c.delete('all')

        w = c.winfo_width()
        h = c.winfo_height()
        if w < 50 or h < 50:
            self.frame.after(self.REDRAW_MS, self._redraw)
            return
        cx, cy = w / 2.0, h / 2.0
        plot_radius = min(w, h) / 2.0 - 30

        with self._scan_lock:
            pts = list(self._latest_points)

        if pts:
            target = max(2.0, math.ceil(max(p[1] for p in pts)))
            target = min(target, LIDAR_MAX_M)
            if target > self._max_range_m or target < self._max_range_m - 1.0:
                self._max_range_m = target
        max_r = max(1.0, self._max_range_m)
        px_per_m = plot_radius / max_r

        ring_step = 1.0 if max_r <= 6.0 else 2.0
        r = ring_step
        while r <= max_r + 1e-6:
            rp = r * px_per_m
            c.create_oval(cx - rp, cy - rp, cx + rp, cy + rp,
                          outline=self.RING_COLOR, width=1)
            c.create_text(cx + rp + 4, cy, text=f'{r:.0f} m',
                          fill=self.LABEL_COLOR, anchor='w',
                          font=('Helvetica', 9))
            r += ring_step

        for ang_deg, label in ((0, '0°'), (90, '90°'), (180, '180°'), (270, '270°')):
            rad = math.radians(ang_deg)
            ex = cx + plot_radius * math.sin(rad)
            ey = cy - plot_radius * math.cos(rad)
            c.create_line(cx, cy, ex, ey, fill=self.AXIS_COLOR, dash=(2, 4))
            lx = cx + (plot_radius + 14) * math.sin(rad)
            ly = cy - (plot_radius + 14) * math.cos(rad)
            c.create_text(lx, ly, text=label, fill=self.LABEL_COLOR,
                          font=('Helvetica', 10, 'bold'))

        sectors: dict[int, tuple[float, float]] = {}
        for ang_deg, d_m in pts:
            rad = math.radians(ang_deg)
            x = cx + d_m * px_per_m * math.sin(rad)
            y = cy - d_m * px_per_m * math.cos(rad)
            c.create_oval(x - 1.5, y - 1.5, x + 1.5, y + 1.5,
                          fill=self.POINT_COLOR, outline='')
            sec = int(ang_deg // self.SECTOR_DEG)
            cur = sectors.get(sec)
            if cur is None or d_m < cur[1]:
                sectors[sec] = (ang_deg, d_m)

        for ang_deg, d_m in sectors.values():
            rad = math.radians(ang_deg)
            x = cx + d_m * px_per_m * math.sin(rad)
            y = cy - d_m * px_per_m * math.cos(rad)
            c.create_oval(x - 3, y - 3, x + 3, y + 3,
                          fill=self.NEAR_COLOR, outline='')
            tx = cx + (d_m * px_per_m + 14) * math.sin(rad)
            ty = cy - (d_m * px_per_m + 14) * math.cos(rad)
            c.create_text(tx, ty, text=f'{d_m:.2f} m',
                          fill=self.LABEL_COLOR, font=('Helvetica', 9))

        c.create_polygon(
            cx,       cy - 10,
            cx - 7,   cy + 7,
            cx + 7,   cy + 7,
            fill=self.ROBOT_COLOR, outline='',
        )

        c.create_text(12, 10, anchor='nw',
                      text=f'{len(pts)} returns  •  range {max_r:.0f} m',
                      fill=FG_DIM, font=('Helvetica', 10))

        self.frame.after(self.REDRAW_MS, self._redraw)

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# Drive controller (keyboard + on-screen D-pad → wifi-server :8080)
# --------------------------------------------------------------------------- #
class DriveController:
    """Tracks which direction keys are held and pumps commands to wifi-server.

    The wifi-server treats each UP/DOWN/LEFT/RIGHT as a *step* (±3 on the
    servo value), so holding a key needs to fire the command repeatedly
    to ramp speed — same cadence as web_drive.py (80 ms).

    X11/Wayland auto-repeat fires KeyRelease+KeyPress pairs while a key
    is held, so a real release is detected by deferring the release for
    AUTOREPEAT_DEBOUNCE_MS; if a fresh KeyPress for the same key arrives
    before the timer fires, the release is cancelled."""

    REPEAT_MS = 80
    AUTOREPEAT_DEBOUNCE_MS = 30

    KEY_TO_CMD = {
        'Up': 'UP', 'w': 'UP', 'W': 'UP',
        'Down': 'DOWN', 's': 'DOWN', 'S': 'DOWN',
        'Left': 'LEFT', 'a': 'LEFT', 'A': 'LEFT',
        'Right': 'RIGHT', 'd': 'RIGHT', 'D': 'RIGHT',
    }
    STOP_KEYSYMS = ('space', 'x', 'X')

    def __init__(self, root: tk.Tk, client: CommandClient,
                 logger: 'Logger | None' = None):
        self._root = root
        self._client = client
        self._logger = logger
        self._active: list[str] = []                          # stack of held cmds
        self._pending_release: dict[str, str] = {}            # cmd -> after_id
        self._repeat_after: str | None = None
        self._lock = threading.Lock()
        self.on_status = lambda txt: None                     # set by panel
        self.on_active_change = lambda active: None           # set by panel

    # ------------------ key binding & event handlers ----------------------- #

    def bind_keys(self, widget: tk.Misc):
        for keysym, cmd in self.KEY_TO_CMD.items():
            widget.bind_all(f'<KeyPress-{keysym}>',
                            lambda _e, c=cmd: self._on_press(c))
            widget.bind_all(f'<KeyRelease-{keysym}>',
                            lambda _e, c=cmd: self._on_release(c))
        for sym in self.STOP_KEYSYMS:
            widget.bind_all(f'<KeyPress-{sym}>', lambda _e: self.emergency_stop())

    def _on_press(self, cmd: str):
        pending = self._pending_release.pop(cmd, None)
        if pending:
            try: self._root.after_cancel(pending)
            except Exception: pass
        if cmd not in self._active:
            self._active.append(cmd)
            self.on_active_change(set(self._active))
            self._start_repeat()

    def _on_release(self, cmd: str):
        prev = self._pending_release.get(cmd)
        if prev:
            try: self._root.after_cancel(prev)
            except Exception: pass
        after_id = self._root.after(self.AUTOREPEAT_DEBOUNCE_MS,
                                    lambda: self._do_release(cmd))
        self._pending_release[cmd] = after_id

    def _do_release(self, cmd: str):
        self._pending_release.pop(cmd, None)
        if cmd in self._active:
            self._active.remove(cmd)
        self.on_active_change(set(self._active))
        if not self._active:
            self._stop_repeat()
            self.send('STOP')

    # ------------------ on-screen pad presses ------------------------------ #

    def press(self, cmd: str):
        """Called by D-pad buttons; behaves like a keypress."""
        self._on_press(cmd)

    def release(self, cmd: str):
        """Called by D-pad buttons; behaves like a keyrelease."""
        self._on_release(cmd)

    def emergency_stop(self):
        self._stop_repeat()
        # Clear any active keys / pending releases so a held key won't
        # resume motion after the STOP.
        for aid in list(self._pending_release.values()):
            try: self._root.after_cancel(aid)
            except Exception: pass
        self._pending_release.clear()
        self._active.clear()
        self.on_active_change(set())
        self.send('STOP')

    # ------------------ repeat loop + send --------------------------------- #

    def _start_repeat(self):
        if self._repeat_after is None:
            self._tick()

    def _stop_repeat(self):
        if self._repeat_after is not None:
            try: self._root.after_cancel(self._repeat_after)
            except Exception: pass
            self._repeat_after = None

    def _tick(self):
        if not self._active:
            self._repeat_after = None
            return
        cmd = self._active[-1]  # most recently pressed wins
        self.send(cmd)
        self._repeat_after = self._root.after(self.REPEAT_MS, self._tick)

    def send(self, cmd: str):
        if self._logger is not None:
            self._logger.add('GUI', f'→ wifi-server: {cmd}')
        def _do():
            ok, reply = self._client.send_cmd(cmd)
            if self._logger is not None:
                if ok:
                    # wifi-server's reply ("OK: UP") tells us what byte it
                    # wrote to the Arduino — log it as the WIFI tag.
                    self._logger.add('WIFI', f'{cmd}: {reply}')
                else:
                    self._logger.add('ERROR', f'{cmd} send failed: {reply}')
            self._root.after_idle(
                lambda: self.on_status(f'{cmd}: {reply}' if ok else f'{cmd} FAILED: {reply}'))
        threading.Thread(target=_do, daemon=True).start()


# --------------------------------------------------------------------------- #
# Drive panel (D-pad + status)
# --------------------------------------------------------------------------- #
class DrivePanel:
    """Right-hand panel: title, on-screen D-pad, STOP, last-reply line."""

    PAD_BG    = '#1e293b'
    PAD_HOVER = '#334155'
    PAD_ACTIVE = ACTIVE
    BTN_FG    = FG

    def __init__(self, parent: tk.Widget, drive: DriveController):
        self._drive = drive
        # No pack_propagate(False) — DrivePanel now sits at the TOP of the
        # right column above the lidar HUD, so we want it to size to its
        # children (height) and stretch to the column (width via fill='x').
        self.frame = tk.Frame(parent, bg=BG)

        tk.Label(self.frame, text='Robot Drive', bg=BG, fg=FG_DIM,
                 font=('Helvetica', 13, 'bold')).pack(side='top', anchor='w',
                                                       padx=14, pady=(12, 4))
        tk.Label(self.frame, text='Arrows / WASD = drive\nSpace or X = STOP',
                 bg=BG, fg=FG_DIM, font=('Helvetica', 10), justify='left'
                 ).pack(side='top', anchor='w', padx=14, pady=(0, 10))

        pad = tk.Frame(self.frame, bg=BG)
        pad.pack(side='top', padx=14, pady=(4, 8))

        BTN_W = 6
        BTN_H = 2
        self._buttons: dict[str, tk.Button] = {}

        def _mk(parent, label, cmd, **grid):
            b = tk.Button(parent, text=label, width=BTN_W, height=BTN_H,
                          bg=self.PAD_BG, fg=self.BTN_FG,
                          activebackground=self.PAD_HOVER, bd=0,
                          font=('Helvetica', 18, 'bold'),
                          takefocus=0)
            b.grid(**grid, padx=4, pady=4)
            b.bind('<ButtonPress-1>',   lambda _e: self._drive.press(cmd))
            b.bind('<ButtonRelease-1>', lambda _e: self._drive.release(cmd))
            # Touch events for Wayland touchscreens — synth as the same.
            b.bind('<Leave>',           lambda _e: self._drive.release(cmd))
            self._buttons[cmd] = b
            return b

        _mk(pad, '▲', 'UP',    row=0, column=1)
        _mk(pad, '◀', 'LEFT',  row=1, column=0)
        _mk(pad, '▶', 'RIGHT', row=1, column=2)
        _mk(pad, '▼', 'DOWN',  row=2, column=1)

        stop = tk.Button(pad, text='STOP', width=BTN_W, height=BTN_H,
                         bg='#dc2626', fg='white',
                         activebackground='#991b1b', bd=0,
                         font=('Helvetica', 11, 'bold'),
                         takefocus=0,
                         command=self._drive.emergency_stop)
        stop.grid(row=1, column=1, padx=4, pady=4)
        self._buttons['STOP'] = stop

        self._status = tk.Label(self.frame, text='—', bg=BG, fg=FG_DIM,
                                font=('Helvetica', 10), anchor='w',
                                justify='left', wraplength=270)
        self._status.pack(side='top', anchor='w', padx=14, pady=(8, 0), fill='x')

        self._active_lbl = tk.Label(self.frame, text='held: none',
                                    bg=BG, fg=FG_DIM,
                                    font=('Helvetica', 10), anchor='w')
        self._active_lbl.pack(side='top', anchor='w', padx=14, pady=(4, 12))

        # --- BLE status box --------------------------------------------------
        ble_box = tk.Frame(self.frame, bg=BG_DARK, bd=0)
        ble_box.pack(side='top', fill='x', padx=14, pady=(8, 0))

        tk.Label(ble_box, text='BLE', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 11, 'bold'), anchor='w'
                 ).pack(side='top', anchor='w', padx=10, pady=(8, 0))
        self._ble_state_lbl = tk.Label(
            ble_box, text='state: unknown (waiting for ble_server)',
            bg=BG_DARK, fg=FG_DIM, font=('Helvetica', 10),
            anchor='w', justify='left', wraplength=300)
        self._ble_state_lbl.pack(side='top', anchor='w', padx=10, pady=(2, 2))
        self._ble_last_lbl = tk.Label(
            ble_box, text='last event: —',
            bg=BG_DARK, fg=FG_DIM, font=('Helvetica', 9),
            anchor='w', justify='left', wraplength=300)
        self._ble_last_lbl.pack(side='top', anchor='w', padx=10, pady=(0, 10))

        drive.on_status = self._on_status
        drive.on_active_change = self._on_active_change

    def _on_status(self, txt: str):
        self._status.config(text=txt)

    def _on_active_change(self, active: set[str]):
        for cmd, btn in self._buttons.items():
            if cmd == 'STOP':
                continue
            if cmd in active:
                btn.config(bg=self.PAD_ACTIVE)
            else:
                btn.config(bg=self.PAD_BG)
        self._active_lbl.config(
            text='held: ' + (', '.join(sorted(active)) if active else 'none'))

    # ----- BLE status (called from BleLogTail via root.after_idle) ---------
    BLE_STATE_COLOR = {
        'connected':    '#22c55e',
        'advertising':  '#fbbf24',
        'disconnected': FG_DIM,
        'unknown':      FG_DIM,
    }

    def set_ble_state(self, state: str, last_line: str = ''):
        color = self.BLE_STATE_COLOR.get(state, FG_DIM)
        self._ble_state_lbl.config(text=f'state: {state}', fg=color)
        if last_line:
            self._ble_last_lbl.config(text=f'last event: {last_line[:160]}')


# --------------------------------------------------------------------------- #
# Routes tab: Training (capture waypoints) + Mission (Claude-piloted run)
# --------------------------------------------------------------------------- #
try:
    from routes import (
        create_route as _routes_create, list_routes as _routes_list,
        load_route as _routes_load, save_waypoint as _routes_save_wp,
        StreamRecorder as _StreamRecorder,
    )
    from vision_navigator import VisionNavigator as _VisionNavigator, MissionRunner as _MissionRunner
    _ROUTES_AVAILABLE = True
    _ROUTES_IMPORT_ERROR = ''
except Exception as e:
    _ROUTES_AVAILABLE = False
    _ROUTES_IMPORT_ERROR = str(e)


class RoutesTab:
    """Two panels side-by-side: Training (capture waypoints from manual
    drive) and Mission (pick a saved route and let Claude pilot it)."""

    def __init__(self, root: tk.Tk, parent: tk.Widget,
                 client: CommandClient,
                 camera_panel: 'CameraPanel | None',
                 lidar_panel: 'LidarPanel | None',
                 logger: 'Logger'):
        self._root = root
        self._client = client
        self._camera_panel = camera_panel
        self._lidar_panel = lidar_panel
        self._logger = logger

        self.frame = tk.Frame(parent, bg=BG)

        if not _ROUTES_AVAILABLE:
            tk.Label(self.frame,
                     text=f'Routes / Mission unavailable: {_ROUTES_IMPORT_ERROR}',
                     bg=BG, fg='#f87171',
                     font=('Helvetica', 11), padx=14, pady=14, wraplength=600
                     ).pack(side='top', anchor='w')
            return

        # Header
        tk.Label(self.frame,
                 text='Routes — train waypoints, then let Claude pilot',
                 bg=BG, fg=FG_DIM, font=('Helvetica', 12, 'bold'),
                 padx=14, pady=10, anchor='w'
                 ).pack(side='top', fill='x')

        body = tk.Frame(self.frame, bg=BG)
        body.pack(side='top', fill='both', expand=True, padx=12, pady=(0, 12))

        # ---- Training (left) -------------------------------------------- #
        train = tk.Frame(body, bg=BG_DARK, bd=0)
        train.pack(side='left', fill='both', expand=True, padx=(0, 6))
        self._build_training_section(train)

        # ---- Mission (right) -------------------------------------------- #
        mission = tk.Frame(body, bg=BG_DARK, bd=0)
        mission.pack(side='right', fill='both', expand=True, padx=(6, 0))
        self._build_mission_section(mission)

        # ---- Status line (bottom) --------------------------------------- #
        self._status_lbl = tk.Label(self.frame, text='', bg=BG, fg=FG_DIM,
                                     font=('Helvetica', 10), anchor='w',
                                     padx=14, pady=4)
        self._status_lbl.pack(side='bottom', fill='x')

        self._refresh_route_list()

    # ----- Training section ------------------------------------------------

    def _build_training_section(self, parent):
        tk.Label(parent, text='TRAINING', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 10, 'bold'), padx=12, pady=8,
                 anchor='w').pack(side='top', fill='x')

        # Route name + create/open
        row = tk.Frame(parent, bg=BG_DARK)
        row.pack(side='top', fill='x', padx=12, pady=4)
        tk.Label(row, text='Route:', bg=BG_DARK, fg=FG,
                 font=('Helvetica', 10)).pack(side='left')
        self._route_name_var = tk.StringVar(value='')
        tk.Entry(row, textvariable=self._route_name_var,
                 bg='#1e293b', fg=FG, insertbackground=FG,
                 font=('Helvetica', 10), width=18, bd=0
                 ).pack(side='left', padx=6)
        tk.Button(row, text='Create / Open', command=self._on_open_route,
                  bg='#1e293b', fg=FG, activebackground='#334155',
                  font=('Helvetica', 10), bd=0, padx=10, pady=4, takefocus=0
                  ).pack(side='left')

        self._current_route_lbl = tk.Label(
            parent, text='No route open.', bg=BG_DARK, fg=FG_DIM,
            font=('Helvetica', 10, 'italic'),
            anchor='w', padx=12, pady=2)
        self._current_route_lbl.pack(side='top', fill='x')

        # Manual capture
        cap_row = tk.Frame(parent, bg=BG_DARK)
        cap_row.pack(side='top', fill='x', padx=12, pady=(8, 2))
        self._capture_btn = tk.Button(
            cap_row, text='📸  Capture Waypoint', command=self._on_capture_waypoint,
            bg=ACCENT, fg=BG, activebackground='#16a34a',
            font=('Helvetica', 11, 'bold'), bd=0, padx=14, pady=6,
            state='disabled', takefocus=0)
        self._capture_btn.pack(side='left')
        self._label_var = tk.StringVar(value='')
        tk.Entry(cap_row, textvariable=self._label_var,
                 bg='#1e293b', fg=FG, insertbackground=FG,
                 font=('Helvetica', 10), width=16, bd=0
                 ).pack(side='left', padx=8)
        tk.Label(cap_row, text='label (optional)', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 9)).pack(side='left')

        # Continuous capture
        cont_row = tk.Frame(parent, bg=BG_DARK)
        cont_row.pack(side='top', fill='x', padx=12, pady=(8, 2))
        self._record_btn = tk.Button(
            cont_row, text='● Start Recording', command=self._on_toggle_recording,
            bg='#1e293b', fg=FG, activebackground='#334155',
            font=('Helvetica', 11, 'bold'), bd=0, padx=14, pady=6,
            state='disabled', takefocus=0)
        self._record_btn.pack(side='left')
        tk.Label(cont_row, text='(2 fps, distills into waypoints on End)',
                 bg=BG_DARK, fg=FG_DIM, font=('Helvetica', 9)
                 ).pack(side='left', padx=8)

        self._wp_list_lbl = tk.Label(parent, text='Waypoints: —', bg=BG_DARK,
                                      fg=FG, font=('Helvetica', 10, 'bold'),
                                      anchor='w', padx=12, pady=6)
        self._wp_list_lbl.pack(side='top', fill='x')

        list_frame = tk.Frame(parent, bg=BG_DARK)
        list_frame.pack(side='top', fill='both', expand=True, padx=12, pady=(2, 10))
        self._wp_listbox = tk.Listbox(
            list_frame, bg='#020617', fg=FG,
            font=('Monospace', 9), bd=0, highlightthickness=0,
            selectbackground='#334155', activestyle='none')
        self._wp_listbox.pack(side='left', fill='both', expand=True)
        wp_scroll = tk.Scrollbar(list_frame, command=self._wp_listbox.yview)
        wp_scroll.pack(side='right', fill='y')
        self._wp_listbox.config(yscrollcommand=wp_scroll.set)

        # State
        self._route = None
        self._recorder = None
        self._record_after_id = None

    def _on_open_route(self):
        name = self._route_name_var.get().strip()
        if not name:
            self._set_status('Enter a route name first.')
            return
        try:
            existing = _routes_list()
            if name in existing:
                self._route = _routes_load(name)
                self._log('INFO', f'opened route "{name}" ({len(self._route.waypoints)} waypoints)')
            else:
                self._route = _routes_create(name)
                self._log('INFO', f'created route "{name}"')
        except Exception as e:
            self._set_status(f'open failed: {e}')
            return
        self._refresh_open_route()
        self._refresh_route_list()
        self._capture_btn.config(state='normal')
        self._record_btn.config(state='normal')

    def _on_capture_waypoint(self):
        if self._route is None or self._camera_panel is None:
            return
        jpeg = self._camera_panel.capture_jpeg_now()
        if not jpeg:
            self._set_status('Capture failed: no camera frame.')
            return
        lidar_pts = (self._lidar_panel.latest_points()
                     if self._lidar_panel is not None else [])
        label = self._label_var.get().strip()
        try:
            wp = _routes_save_wp(self._route, jpeg, lidar_pts, label=label)
        except Exception as e:
            self._set_status(f'save failed: {e}')
            return
        self._label_var.set('')
        self._log('INFO', f'captured waypoint #{wp.idx} ({wp.label})')
        self._refresh_open_route()

    def _on_toggle_recording(self):
        if self._route is None:
            return
        if self._recorder is None:
            try:
                self._recorder = _StreamRecorder.begin(self._route)
            except Exception as e:
                self._set_status(f'start recording failed: {e}')
                return
            self._record_btn.config(text='■  End Recording', bg='#dc2626', fg='white')
            self._log('INFO', f'recording stream to {self._recorder.stream_dir}')
            self._record_tick()
        else:
            rec = self._recorder
            self._recorder = None
            if self._record_after_id:
                try: self.frame.after_cancel(self._record_after_id)
                except Exception: pass
            self._record_after_id = None
            self._record_btn.config(text='● Start Recording', bg='#1e293b', fg=FG)
            self._set_status(f'distilling {rec.frame_count} frames into waypoints…')
            try:
                picks = rec.distill_waypoints()
            except Exception as e:
                self._set_status(f'distill failed: {e}')
                return
            self._log('INFO',
                f'stream {rec.recording_id}: {rec.frame_count} frames → '
                f'{len(picks)} new waypoints')
            # Refresh route to pick up new waypoints written to disk.
            try:
                self._route = _routes_load(self._route.name)
            except Exception:
                pass
            self._refresh_open_route()

    def _record_tick(self):
        if self._recorder is None or self._camera_panel is None:
            return
        try:
            jpeg = self._camera_panel.capture_jpeg_now()
            lidar_pts = (self._lidar_panel.latest_points()
                         if self._lidar_panel is not None else [])
            if jpeg:
                self._recorder.push(jpeg, lidar_pts)
        except Exception as e:
            self._log('ERROR', f'stream frame failed: {e}')
        # 2 fps cadence; trims disk I/O while still capturing motion.
        self._record_after_id = self.frame.after(500, self._record_tick)

    def _refresh_open_route(self):
        if self._route is None:
            self._current_route_lbl.config(text='No route open.')
            self._wp_list_lbl.config(text='Waypoints: —')
            self._wp_listbox.delete(0, 'end')
            return
        self._current_route_lbl.config(
            text=f'Open: {self._route.name}   ({self._route.root})')
        self._wp_list_lbl.config(text=f'Waypoints: {len(self._route.waypoints)}')
        self._wp_listbox.delete(0, 'end')
        for w in self._route.waypoints:
            front = w.lidar.get('sectors_m', {}).get('fwd') if w.lidar else None
            front_s = f'{front:.2f}m' if isinstance(front, (int, float)) else '—'
            self._wp_listbox.insert(
                'end', f'#{w.idx:03d}  {w.label[:22]:22}  fwd={front_s}')

    # ----- Mission section -------------------------------------------------

    def _build_mission_section(self, parent):
        tk.Label(parent, text='MISSION  —  Claude pilots', bg=BG_DARK, fg=FG_DIM,
                 font=('Helvetica', 10, 'bold'), padx=12, pady=8,
                 anchor='w').pack(side='top', fill='x')

        pick_row = tk.Frame(parent, bg=BG_DARK)
        pick_row.pack(side='top', fill='x', padx=12, pady=4)
        tk.Label(pick_row, text='Route:', bg=BG_DARK, fg=FG,
                 font=('Helvetica', 10)).pack(side='left')
        self._mission_route_var = tk.StringVar(value='')
        self._mission_route_menu = ttk.Combobox(
            pick_row, textvariable=self._mission_route_var,
            state='readonly', width=22, font=('Helvetica', 10))
        self._mission_route_menu.pack(side='left', padx=6)
        tk.Button(pick_row, text='⟳', command=self._refresh_route_list,
                  bg='#1e293b', fg=FG, activebackground='#334155',
                  font=('Helvetica', 10), bd=0, padx=8, pady=2, takefocus=0
                  ).pack(side='left')

        btn_row = tk.Frame(parent, bg=BG_DARK)
        btn_row.pack(side='top', fill='x', padx=12, pady=(10, 4))
        self._start_btn = tk.Button(
            btn_row, text='▶ Start Mission', command=self._on_start_mission,
            bg=ACCENT, fg=BG, activebackground='#16a34a',
            font=('Helvetica', 11, 'bold'), bd=0, padx=14, pady=6, takefocus=0)
        self._start_btn.pack(side='left')
        self._abort_btn = tk.Button(
            btn_row, text='■ Abort', command=self._on_abort_mission,
            bg='#dc2626', fg='white', activebackground='#991b1b',
            font=('Helvetica', 11, 'bold'), bd=0, padx=14, pady=6,
            state='disabled', takefocus=0)
        self._abort_btn.pack(side='left', padx=8)

        # Live status grid
        grid = tk.Frame(parent, bg=BG_DARK)
        grid.pack(side='top', fill='x', padx=12, pady=10)
        self._mission_state_lbl = tk.Label(
            grid, text='State: idle', bg=BG_DARK, fg=FG_DIM,
            font=('Helvetica', 10, 'bold'), anchor='w')
        self._mission_state_lbl.grid(row=0, column=0, sticky='w', pady=2)
        self._mission_leg_lbl = tk.Label(
            grid, text='Leg: —', bg=BG_DARK, fg=FG_DIM,
            font=('Helvetica', 10), anchor='w')
        self._mission_leg_lbl.grid(row=1, column=0, sticky='w', pady=2)
        self._mission_wp_lbl = tk.Label(
            grid, text='Waypoint: —', bg=BG_DARK, fg=FG_DIM,
            font=('Helvetica', 10), anchor='w')
        self._mission_wp_lbl.grid(row=2, column=0, sticky='w', pady=2)
        self._mission_calls_lbl = tk.Label(
            grid, text='Calls: 0/—', bg=BG_DARK, fg=FG_DIM,
            font=('Helvetica', 10), anchor='w')
        self._mission_calls_lbl.grid(row=3, column=0, sticky='w', pady=2)
        self._mission_action_lbl = tk.Label(
            parent, text='Last action: —', bg=BG_DARK, fg='#60a5fa',
            font=('Helvetica', 10, 'bold'), padx=12, pady=2, anchor='w',
            justify='left', wraplength=380)
        self._mission_action_lbl.pack(side='top', fill='x')
        self._mission_reason_lbl = tk.Label(
            parent, text='Reasoning: —', bg=BG_DARK, fg=FG,
            font=('Helvetica', 10), padx=12, pady=6, anchor='w',
            justify='left', wraplength=380)
        self._mission_reason_lbl.pack(side='top', fill='x')

        self._runner = None

    def _refresh_route_list(self):
        try:
            names = _routes_list()
        except Exception:
            names = []
        self._mission_route_menu['values'] = names
        if names and not self._mission_route_var.get():
            self._mission_route_var.set(names[0])

    def _on_start_mission(self):
        name = self._mission_route_var.get().strip()
        if not name:
            self._set_status('Pick a route first.')
            return
        if self._runner is not None and self._runner.is_running():
            self._set_status('A mission is already running.')
            return
        try:
            route = _routes_load(name)
        except Exception as e:
            self._set_status(f'load failed: {e}')
            return
        if not route.waypoints:
            self._set_status(f'Route "{name}" has no waypoints to navigate to.')
            return
        if self._camera_panel is None:
            self._set_status('Camera panel unavailable; mission needs camera.')
            return
        runner = _MissionRunner(
            route=route,
            client=self._client,
            frame_getter=self._camera_panel.capture_jpeg_now,
            lidar_getter=(self._lidar_panel.latest_points
                          if self._lidar_panel is not None else (lambda: [])),
            on_status=lambda s: self._root.after_idle(
                lambda: self._on_mission_status(s)),
            on_log=lambda tag, msg: self._logger.add(tag, f'[mission] {msg}'),
            lawn_photo_cb=self._camera_panel.trigger_lawn_check,
        )
        self._runner = runner
        self._abort_btn.config(state='normal')
        self._start_btn.config(state='disabled')
        runner.start()
        self._logger.add('INFO', f'mission "{name}" started ({len(route.waypoints)} waypoints)')
        self._set_status(f'Mission "{name}" running…')

    def _on_abort_mission(self):
        if self._runner is None:
            return
        self._runner.abort()
        self._abort_btn.config(state='disabled')

    def _on_mission_status(self, s):
        self._mission_state_lbl.config(text=f'State: {s.state}')
        self._mission_leg_lbl.config(text=f'Leg: {s.leg or "—"}')
        self._mission_wp_lbl.config(
            text=f'Waypoint: {s.current_wp + 1 if s.total_wp else "—"}/{s.total_wp}')
        self._mission_calls_lbl.config(text=f'Calls: {s.calls}/{s.calls_max}')
        if s.last_action:
            self._mission_action_lbl.config(text=f'Last action: {s.last_action}')
        if s.last_reasoning:
            self._mission_reason_lbl.config(text=f'Reasoning: {s.last_reasoning}')
        if s.state in ('completed', 'aborted', 'failed'):
            self._start_btn.config(state='normal')
            self._abort_btn.config(state='disabled')

    # ----- helpers ---------------------------------------------------------

    def _set_status(self, msg):
        try:
            self._status_lbl.config(text=msg)
        except Exception:
            pass

    def _log(self, tag, msg):
        try:
            self._logger.add(tag, f'[routes] {msg}')
        except Exception:
            pass
        self._set_status(msg)


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description='Pi desktop GUI for wifi mode (camera + lidar + drive).')
    parser.add_argument('--no-camera', action='store_true', help='Skip the camera panel.')
    parser.add_argument('--no-lidar',  action='store_true', help='Skip the lidar panel.')
    parser.add_argument('--no-flip',   action='store_true',
                        help='Do not rotate the camera image 180° (use if you remount the camera).')
    parser.add_argument('--lidar-port', default='/dev/ttyUSB0',
                        help='Serial port for the RPLidar (default: /dev/ttyUSB0).')
    parser.add_argument('--camera-fps', type=int, default=5,
                        help='Live preview frame rate (default: 5).')
    parser.add_argument('--camera-width',  type=int, default=640)
    parser.add_argument('--camera-height', type=int, default=480)
    parser.add_argument('--cmd-host', default='127.0.0.1',
                        help='wifi-server host (default: 127.0.0.1).')
    parser.add_argument('--cmd-port', type=int, default=8080,
                        help='wifi-server port (default: 8080).')
    parser.add_argument('--ble-log', default=None,
                        help='Path to the BLE bridge log file to tail. '
                             'Lines prefixed with "[ble]" become BLE log entries '
                             'and connect/disconnect events update the BLE status box.')
    parser.add_argument('--ble-relay-port', type=int, default=8081,
                        help='Local TCP port for the BLE bridge to forward '
                             'commands to (default: 8081). Set to 0 to disable.')
    parser.add_argument('--frame-pub-port', type=int, default=8082,
                        help='Local TCP port that publishes JPEG video frames '
                             'for the BLE bridge to subscribe to (default: 8082). '
                             'Set to 0 to disable.')
    args = parser.parse_args()

    camera = None
    if not args.no_camera:
        camera = CameraSource(
            video_size=(args.camera_width, args.camera_height),
            photo_size=(1280, 960),
            video_quality=85,
            photo_quality=85,
        )

    lidar = None
    if not args.no_lidar:
        lidar = LidarSource(port=args.lidar_port)

    client = CommandClient(args.cmd_host, args.cmd_port)

    root = tk.Tk()
    root.title('Robot Console')
    root.configure(bg=BG)
    width = 0
    if not args.no_camera: width += 700
    if not args.no_lidar:  width += 560
    width += 380
    root.geometry(f'{max(900, width)}x820')

    # ttk theme so the Notebook tabs respect our dark palette.
    style = ttk.Style()
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass
    style.configure('TNotebook', background=BG, borderwidth=0)
    style.configure('TNotebook.Tab', background='#1e293b', foreground=FG,
                    padding=(16, 6), font=('Helvetica', 11, 'bold'),
                    borderwidth=0)
    style.map('TNotebook.Tab',
              background=[('selected', ACTIVE)],
              foreground=[('selected', '#ffffff')])

    notebook = ttk.Notebook(root)
    notebook.pack(fill='both', expand=True)

    # --- Drive tab -------------------------------------------------------- #
    # Layout: camera fills the left side (full height); right column splits
    # vertically — drive panel on top, lidar overhead view (square) on the
    # bottom right.
    drive_tab = tk.Frame(notebook, bg=BG)
    notebook.add(drive_tab, text='Drive')

    main_frame = tk.Frame(drive_tab, bg=BG)
    main_frame.pack(fill='both', expand=True)
    main_frame.pack_propagate(False)

    # We construct the Logger first so the publisher / panels can use it.
    # (Tabs are created right after the camera panel below.)

    cam_panel = None
    # Frame publisher is created later (it needs the Logger), but we keep
    # a slot here so the CameraPanel can be wired to it once it exists.

    right_col = tk.Frame(main_frame, bg=BG, width=380)
    right_col.pack(side='right', fill='y')
    right_col.pack_propagate(False)

    lidar_panel = None
    if not args.no_lidar:
        # Pack the lidar at the BOTTOM of the right column so it sits in
        # the lower-right of the window, like a HUD overhead view. The
        # 360x360 box keeps the polar plot roughly square.
        lidar_panel = LidarPanel(root, right_col, lidar)
        # Override the panel frame's default width (set to 560 inside the
        # class for the old side-by-side layout) so it fits the column.
        lidar_panel.frame.config(width=360, height=360)
        lidar_panel.frame.pack(side='bottom', fill='x', pady=(8, 0))

    # --- Routes tab (Training + Mission) ---------------------------------- #
    # Created later (after Logger + CameraPanel + LidarPanel exist) but
    # added to the Notebook in tab order before Logs.

    # --- Logs tab + Logger (built first so DriveController can log) ------- #
    logs_tab = tk.Frame(notebook, bg=BG)
    notebook.add(logs_tab, text='Logs')
    logger = Logger(logs_tab)
    logger.frame.pack(fill='both', expand=True)
    logger.add('INFO', 'wifi-desktop starting…')

    # JPEG frame publisher — BLE bridge subscribes here so phones get video
    # while the GUI keeps owning the camera.
    frame_publisher = None
    if args.frame_pub_port and args.frame_pub_port > 0 and not args.no_camera:
        frame_publisher = JpegFramePublisher('127.0.0.1', args.frame_pub_port, logger)
        frame_publisher.start()

    # Now actually build the CameraPanel (wired to the publisher) and put
    # it on the Drive tab.
    if not args.no_camera:
        cam_panel = CameraPanel(root, main_frame, camera,
                                fps=args.camera_fps, flip=not args.no_flip,
                                frame_publisher=frame_publisher)
        cam_panel.frame.pack(side='left', fill='both', expand=True,
                             before=right_col)

    drive = DriveController(root, client, logger=logger)
    drive_panel = DrivePanel(right_col, drive)
    drive_panel.frame.pack(side='top', fill='x')
    drive.bind_keys(root)

    # Routes tab now that we have client + camera + lidar panels + logger.
    routes_tab = tk.Frame(notebook, bg=BG)
    # Insert between Drive (index 0) and Logs (index 1).
    notebook.insert(1, routes_tab, text='Routes')
    routes_panel = RoutesTab(root, routes_tab, client, cam_panel, lidar_panel, logger)
    routes_panel.frame.pack(fill='both', expand=True)

    # --- BLE relay (so BLE commands can reach wifi-server through us) ----- #
    relay = None
    if args.ble_relay_port and args.ble_relay_port > 0:
        def _on_mission_cmd(verb, arg):
            """Bridge MISSION verbs from BLE to the Routes tab. Runs on the
            relay's worker thread — re-enter Tk via after_idle for any UI
            mutations; everything we need to call here is thread-safe."""
            try:
                if verb == 'MISSION':
                    if not arg:
                        return False, 'ERR: MISSION needs a route name'
                    # Pre-load the route on the worker thread so we get a
                    # synchronous error reply if it doesn't exist.
                    try:
                        rt = _routes_load(arg)
                    except Exception as e:
                        return False, f'ERR: no such route ({e})'
                    if not rt.waypoints:
                        return False, 'ERR: route has no waypoints'
                    # Kick the GUI to start. routes_panel uses after_idle
                    # internally for Tk updates; runner.start() itself is
                    # just thread spawning so calling from here is safe.
                    routes_panel._mission_route_var.set(arg)
                    root.after_idle(routes_panel._on_start_mission)
                    return True, f'OK: starting mission {arg} ({len(rt.waypoints)} wp)'
                elif verb == 'MISSION-ABORT':
                    if routes_panel._runner is None:
                        return False, 'ERR: no mission running'
                    routes_panel._runner.abort()
                    return True, 'OK: abort sent'
                elif verb == 'MISSION-STATUS':
                    if routes_panel._runner is None:
                        return True, 'OK: state=idle'
                    s = routes_panel._runner.status
                    return True, (
                        f'OK: state={s.state} leg={s.leg or "-"} '
                        f'wp={s.current_wp + 1}/{s.total_wp} '
                        f'calls={s.calls}/{s.calls_max}')
                else:
                    return False, f'ERR: unknown mission verb {verb!r}'
            except Exception as e:
                return False, f'ERR: {e}'

        relay = BleRelay('127.0.0.1', args.ble_relay_port, client, logger,
                         on_command=lambda cmd, reply: root.after_idle(
                             lambda: drive_panel.set_ble_state(
                                 'connected', f'{cmd} → {reply}')),
                         on_mission=_on_mission_cmd)
        relay.start()

    # --- BLE log tail (optional) ------------------------------------------ #
    ble_tail = None
    if args.ble_log:
        def _on_ble_state(state, line):
            root.after_idle(lambda: drive_panel.set_ble_state(state, line))
        ble_tail = BleLogTail(args.ble_log, logger, _on_ble_state)
        ble_tail.start()
        logger.add('INFO', f'tailing BLE log: {args.ble_log}')
    else:
        drive_panel.set_ble_state('disabled', '(--ble-log not set; BLE bridge unmonitored)')
        logger.add('INFO', 'BLE log tail disabled (no --ble-log)')

    # Probe wifi-server so the status line shows reality at startup.
    def _probe():
        ok, reply = client.send_cmd('STATUS')
        root.after_idle(lambda: drive_panel._on_status(
            f'connected: {reply}' if ok else f'wifi-server unreachable: {reply}'))
        logger.add('WIFI' if ok else 'ERROR',
                   f'STATUS probe: {reply}' if ok else f'STATUS probe failed: {reply}')
    threading.Thread(target=_probe, daemon=True).start()

    root.protocol('WM_DELETE_WINDOW', root.quit)
    root.focus_force()

    try:
        root.mainloop()
    finally:
        if ble_tail:
            ble_tail.stop()
        if relay:
            relay.stop()
        if frame_publisher:
            frame_publisher.stop()
        if cam_panel:
            cam_panel.close()
        if lidar_panel:
            lidar_panel.close()
        if lidar:
            lidar.stop()
        if camera:
            camera.close()


if __name__ == '__main__':
    main()
