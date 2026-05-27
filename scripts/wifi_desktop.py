#!/usr/bin/env python3
"""Pi desktop GUI for WiFi-mode operation.

Spawns two top-level Tk windows on the Pi's desktop so the operator can
see what the robot sees while driving over WiFi from the same machine:

  1. "Robot Camera" — live IMX519 preview at ~5 fps.
  2. "Robot Lidar"  — top-down polar plot of the latest RPLidar scan.
                      Robot sits at the centre, range rings every 1 m,
                      and the nearest obstacle in each 30° sector is
                      annotated with its distance in metres.

Hardware reuse: CameraSource and LidarSource are imported straight from
scripts/ble_server.py — there's only one camera and one lidar attached
to the Pi, so wifi-desktop and ble_server cannot run simultaneously
(scripts/run-wifi-desktop.sh handles that).

Usage:
    python3 scripts/wifi_desktop.py
    python3 scripts/wifi_desktop.py --no-lidar    # camera only
    python3 scripts/wifi_desktop.py --no-camera   # lidar only

Run alongside `bin/robot wifi-server`. The wrapper script
scripts/run-wifi-desktop.sh starts both for you.
"""

from __future__ import annotations

import argparse
import math
import os
import queue
import sys
import threading
import time
import tkinter as tk

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from ble_server import CameraSource, LidarSource  # type: ignore
except Exception as e:
    sys.exit(f'Could not import CameraSource/LidarSource from ble_server.py: {e}\n'
             'Make sure scripts/ble_server.py is present and unmodified.')

# Grass-health helpers live in lawn_camera.py — same scripts/ directory, so
# the import is cheap.  We tolerate the file or its deps being missing
# (PIL / anthropic SDK) so the GUI still works for camera + map without
# the lawn-check button.
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


# We treat lidar returns shorter than this as the chassis / specular
# noise, and longer than this as out of the A1's useful range.
LIDAR_MIN_M = 0.10
LIDAR_MAX_M = 12.0


# --------------------------------------------------------------------------- #
# Camera window
# --------------------------------------------------------------------------- #
class CameraWindow:
    """Tk top-level showing live camera frames.

    Tk widgets are not thread-safe, so a background thread pulls frames
    from picamera2 into a tiny queue (size 1, latest-wins) and the Tk
    main thread reads from it via .after().  Decoding picamera2's RGB
    array → PIL.Image → ImageTk.PhotoImage all happens on the GUI
    thread, which is fast at our 5 fps target."""

    def __init__(self, root: tk.Tk, camera: CameraSource | None, fps: int = 5):
        self._root = root
        self._camera = camera
        self._fps = max(1, min(15, int(fps)))
        self._stop = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=1)
        self._lawn_busy = False

        self.top = tk.Toplevel(root)
        self.top.title('Robot Camera')
        self.top.configure(bg='#0f172a')
        self.top.geometry('640x580')

        self._label_status = tk.Label(
            self.top, text='', fg='#94a3b8', bg='#0f172a',
            font=('Helvetica', 11), anchor='w', padx=10, pady=6,
        )
        self._label_status.pack(side='top', fill='x')

        self._label_image = tk.Label(self.top, bg='#020617')
        self._label_image.pack(side='top', fill='both', expand=True, padx=8, pady=(0, 8))

        # Control strip: lawn-check + camera port switch + camera info.
        # All buttons fire-and-forget — they print verbose diagnostics to
        # stderr so the operator can debug from the same terminal that
        # launched the GUI.
        ctrl = tk.Frame(self.top, bg='#0f172a')
        ctrl.pack(side='top', fill='x', padx=8, pady=(0, 8))
        self._btn_lawn = tk.Button(
            ctrl, text='🌱 Lawn Check (Claude)', command=self._on_lawn_check,
            bg='#22c55e', fg='#0f172a', activebackground='#16a34a',
            font=('Helvetica', 11, 'bold'), bd=0, padx=12, pady=6,
        )
        self._btn_lawn.pack(side='left', padx=(0, 6))
        self._btn_cam0 = tk.Button(
            ctrl, text='Port 0', command=lambda: self._on_switch_port(0),
            bg='#1e293b', fg='#e2e8f0', activebackground='#334155',
            font=('Helvetica', 10), bd=0, padx=10, pady=6,
        )
        self._btn_cam0.pack(side='left', padx=2)
        self._btn_cam1 = tk.Button(
            ctrl, text='Port 1', command=lambda: self._on_switch_port(1),
            bg='#1e293b', fg='#e2e8f0', activebackground='#334155',
            font=('Helvetica', 10), bd=0, padx=10, pady=6,
        )
        self._btn_cam1.pack(side='left', padx=2)
        self._btn_caminfo = tk.Button(
            ctrl, text='Camera Info', command=self._on_camera_info,
            bg='#1e293b', fg='#e2e8f0', activebackground='#334155',
            font=('Helvetica', 10), bd=0, padx=10, pady=6,
        )
        self._btn_caminfo.pack(side='left', padx=2)

        if not _LAWN_AVAILABLE:
            self._btn_lawn.config(state='disabled', bg='#334155', fg='#64748b')

        self._photo = None      # keep a strong ref so Tk doesn't GC the PhotoImage

        # Lazy import — only the camera window depends on PIL.
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

        self._set_status(f'Camera ready ({fps} fps).')
        self._thread = threading.Thread(target=self._capture_loop, daemon=True,
                                        name='wifi-desktop-camera')
        self._thread.start()
        self.top.after(50, self._drain)

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
            dt = time.monotonic() - t0
            if dt < period:
                time.sleep(period - dt)

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
                w = max(160, self._label_image.winfo_width())
                h = max(120, self._label_image.winfo_height())
                src_w, src_h = img.size
                scale = min(w / src_w, h / src_h)
                tw, th = max(1, int(src_w * scale)), max(1, int(src_h * scale))
                img = img.resize((tw, th), self._Image.BILINEAR)
                self._photo = self._ImageTk.PhotoImage(img)
                self._label_image.config(image=self._photo)
            except Exception as e:
                self._set_status(f'Display error: {e}')
        self.top.after(50, self._drain)

    # ----- Camera-port + lawn-check controls --------------------------------

    def _on_switch_port(self, port):
        """Stop the live preview, switch CSI port on the shared CameraSource,
        and resume.  Prints every step to stderr so debugging which port
        the sensor is actually on doesn't require digging into the BLE
        replies in parallel.

        Runs synchronously on the GUI thread: set_camera_num re-opens
        picamera2 which can take ~1 s; that's a noticeable hitch in the
        Tk loop but acceptable for a manual debug action."""
        if self._camera is None:
            self._set_status(f'Cannot switch to port {port}: --no-camera at startup.')
            return
        print(f'[desktop] requesting camera port switch to {port}…', file=sys.stderr)
        self._set_status(f'Switching to port {port}…')
        # Briefly stop the queue-drain by signalling a soft pause: drain
        # whatever's queued so we don't render a half-frame after re-open.
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
        """Print the full libcamera enumeration to stderr and update the
        status bar with a compact summary.  This is the headline diagnostic
        for 'why can't the Pi see my camera?' — if the port the ribbon is
        plugged into doesn't appear here, the problem is electrical /
        dtoverlay, not picamera2."""
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

    def _on_lawn_check(self):
        """Capture a high-res still and ask Claude for a grass-health verdict.

        Runs the capture + API call on a background thread so the Tk loop
        keeps repainting.  Result is delivered back to the main thread
        via after_idle(), which then pops a Toplevel showing the photo
        and Claude's verdict — plus dumps the full structured response
        to stderr so the operator has the same data in their terminal."""
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
        self._btn_lawn.config(state='disabled', text='🌱 Working…', bg='#334155', fg='#94a3b8')
        self._set_status('Lawn check: capturing photo + asking Claude…')
        print('[desktop] LAWN CHECK starting…', file=sys.stderr)

        def _work():
            try:
                jpeg = self._camera.capture_photo_jpeg()
                if not jpeg:
                    raise RuntimeError('Photo capture returned no bytes.')
                # encode_for_api wants a file path; write to a tempfile.
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
        self._btn_lawn.config(state='normal', text='🌱 Lawn Check (Claude)',
                              bg='#22c55e', fg='#0f172a')
        self._set_status('Lawn check complete — see popup.')
        # Dump the full structured result to the console for the operator.
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
        self._btn_lawn.config(state='normal', text='🌱 Lawn Check (Claude)',
                              bg='#22c55e', fg='#0f172a')
        self._set_status(f'Lawn check failed: {msg}')

    def close(self):
        self._stop.set()


class LawnResultPopup:
    """Modal-ish Tk Toplevel that shows the JPEG + the grass-health verdict.

    Not actually modal — tkinter's grab_set on a Toplevel often misbehaves
    on Linux desktops — but it's a distinct window that sits on top so
    the operator can read the verdict without dismissing the camera
    preview.  Closes with the X button or by hitting Escape."""

    def __init__(self, root: tk.Tk, jpeg_bytes: bytes, result: dict):
        self.top = tk.Toplevel(root)
        self.top.title('Lawn Check Result')
        self.top.configure(bg='#0f172a')
        self.top.geometry('560x720')
        self.top.bind('<Escape>', lambda _e: self.top.destroy())

        # Header — colour-coded health bucket.
        score = result.get('health_score')
        present = bool(result.get('lawn_present'))
        if not present:
            bucket_label = 'No lawn detected'
            bucket_color = '#94a3b8'
            score_text = 'n/a'
        else:
            try:
                s = int(score) if score is not None else 0
            except Exception:
                s = 0
            if s <= 30:    bucket_label, bucket_color = 'Poor', '#ef4444'
            elif s <= 75:  bucket_label, bucket_color = 'Fair', '#eab308'
            else:          bucket_label, bucket_color = 'Healthy', '#22c55e'
            score_text = f'{max(0, min(100, s))} / 100'

        hdr = tk.Frame(self.top, bg='#0f172a')
        hdr.pack(side='top', fill='x', padx=14, pady=(14, 6))
        tk.Label(hdr, text='LAWN HEALTH', bg='#0f172a', fg='#94a3b8',
                 font=('Helvetica', 10, 'bold')).pack(side='left')
        tk.Label(hdr, text=score_text, bg='#0f172a', fg=bucket_color,
                 font=('Helvetica', 22, 'bold')).pack(side='right')

        tk.Label(self.top, text=bucket_label, bg='#0f172a', fg=bucket_color,
                 font=('Helvetica', 12, 'bold')).pack(side='top', anchor='w', padx=14)

        # Image
        try:
            from PIL import Image, ImageTk  # type: ignore
            import io
            img = Image.open(io.BytesIO(jpeg_bytes))
            # Constrain to ~520 px wide so it fits.
            w, h = img.size
            scale = min(520.0 / w, 360.0 / h, 1.0)
            img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.BILINEAR)
            self._photo = ImageTk.PhotoImage(img)
            tk.Label(self.top, image=self._photo, bg='#020617',
                     borderwidth=0).pack(side='top', padx=14, pady=10)
        except Exception as e:
            tk.Label(self.top, text=f'(image preview failed: {e})',
                     bg='#0f172a', fg='#94a3b8').pack(side='top', padx=14, pady=10)

        # Verdict + recs scroll area.
        body = tk.Frame(self.top, bg='#0f172a')
        body.pack(side='top', fill='both', expand=True, padx=14, pady=(0, 8))

        summary = (result.get('summary') or '').strip() or '(no summary returned)'
        tk.Label(body, text='Summary', bg='#0f172a', fg='#94a3b8',
                 font=('Helvetica', 10, 'bold'), anchor='w').pack(fill='x')
        tk.Message(body, text=summary, width=520, bg='#0f172a', fg='#e2e8f0',
                   font=('Helvetica', 11)).pack(fill='x', pady=(0, 8))

        issues = result.get('issues') or []
        if issues:
            tk.Label(body, text='Issues', bg='#0f172a', fg='#94a3b8',
                     font=('Helvetica', 10, 'bold'), anchor='w').pack(fill='x')
            tk.Message(body, text='• ' + '\n• '.join(issues), width=520,
                       bg='#0f172a', fg='#fcd34d',
                       font=('Helvetica', 10)).pack(fill='x', pady=(0, 8))

        recs = result.get('recommendations') or []
        if recs:
            tk.Label(body, text='Recommendations', bg='#0f172a', fg='#94a3b8',
                     font=('Helvetica', 10, 'bold'), anchor='w').pack(fill='x')
            tk.Message(body, text='• ' + '\n• '.join(recs), width=520,
                       bg='#0f172a', fg='#86efac',
                       font=('Helvetica', 10)).pack(fill='x', pady=(0, 8))

        # Metadata footer.
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
                     bg='#0f172a', fg='#64748b',
                     font=('Helvetica', 9), anchor='w').pack(side='bottom', fill='x',
                                                              padx=14, pady=(0, 12))

        tk.Button(self.top, text='Close', command=self.top.destroy,
                  bg='#1e293b', fg='#e2e8f0', activebackground='#334155',
                  font=('Helvetica', 10), bd=0, padx=14, pady=6).pack(
            side='bottom', anchor='e', padx=14, pady=4)


# --------------------------------------------------------------------------- #
# Lidar polar plot window
# --------------------------------------------------------------------------- #
class LidarWindow:
    """Tk top-level showing a top-down polar plot of the RPLidar scan.

    The robot is drawn at the canvas centre as a small triangle pointing
    'forward' (0°, up).  Concentric rings mark distance in metres.  Lidar
    returns are drawn as small dots; the closest hit inside each 30°
    sector gets a text label with its distance.

    Tk is not thread-safe, so the rplidar callback only stashes the
    latest scan under a lock — the Tk main loop redraws via after()."""

    # Visual tuning
    BG          = '#0f172a'
    RING_COLOR  = '#1e293b'
    AXIS_COLOR  = '#334155'
    POINT_COLOR = '#38bdf8'
    NEAR_COLOR  = '#fbbf24'   # nearest-in-sector marker
    LABEL_COLOR = '#e2e8f0'
    ROBOT_COLOR = '#22c55e'
    SECTOR_DEG  = 30          # one distance label per N° sector
    REDRAW_MS   = 200         # ~5 Hz

    def __init__(self, root: tk.Tk, lidar: LidarSource | None):
        self._latest_points: list[tuple[float, float]] = []   # (angle_deg, dist_m)
        self._scan_lock = threading.Lock()
        self._max_range_m = 5.0   # auto-grows up to LIDAR_MAX_M

        self.top = tk.Toplevel(root)
        self.top.title('Robot Lidar')
        self.top.configure(bg=self.BG)
        self.top.geometry('720x760')

        self._status = tk.Label(
            self.top, text='', fg='#94a3b8', bg=self.BG,
            font=('Helvetica', 11), anchor='w', padx=10, pady=6,
        )
        self._status.pack(side='top', fill='x')

        self._canvas = tk.Canvas(self.top, bg=self.BG, highlightthickness=0)
        self._canvas.pack(side='top', fill='both', expand=True, padx=8, pady=(0, 8))

        if lidar is not None and lidar.available:
            lidar._on_scan = self._on_scan   # noqa: SLF001 — same hook BLE streamer uses
            lidar.start()
            self._status.config(text='Lidar running — distances in metres, robot at centre, 0° = forward.')
        else:
            note = lidar.reason if lidar is not None else 'disabled'
            self._status.config(text=f'Lidar: {note}')

        self.top.after(self.REDRAW_MS, self._redraw)

    def _on_scan(self, points):
        usable = []
        for ang, dist_mm in points:
            d_m = dist_mm / 1000.0
            if d_m < LIDAR_MIN_M or d_m > LIDAR_MAX_M:
                continue
            usable.append((float(ang) % 360.0, d_m))
        with self._scan_lock:
            self._latest_points = usable

    def _redraw(self):
        c = self._canvas
        c.delete('all')

        w = c.winfo_width()
        h = c.winfo_height()
        if w < 50 or h < 50:
            self.top.after(self.REDRAW_MS, self._redraw)
            return
        cx, cy = w / 2.0, h / 2.0
        plot_radius = min(w, h) / 2.0 - 30

        with self._scan_lock:
            pts = list(self._latest_points)

        # Auto-grow the plot range so distant returns stay on-screen, but
        # never shrink below 2 m (avoids jumpy axes for tiny rooms).
        if pts:
            target = max(2.0, math.ceil(max(p[1] for p in pts)))
            target = min(target, LIDAR_MAX_M)
            # gentle hysteresis — only adjust when far off
            if target > self._max_range_m or target < self._max_range_m - 1.0:
                self._max_range_m = target
        max_r = max(1.0, self._max_range_m)
        px_per_m = plot_radius / max_r

        # --- range rings + labels ----------------------------------------- #
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

        # --- cardinal spokes (0/90/180/270) ------------------------------- #
        for ang_deg, label in ((0, '0°'), (90, '90°'), (180, '180°'), (270, '270°')):
            rad = math.radians(ang_deg)
            ex = cx + plot_radius * math.sin(rad)
            ey = cy - plot_radius * math.cos(rad)
            c.create_line(cx, cy, ex, ey, fill=self.AXIS_COLOR, dash=(2, 4))
            lx = cx + (plot_radius + 14) * math.sin(rad)
            ly = cy - (plot_radius + 14) * math.cos(rad)
            c.create_text(lx, ly, text=label, fill=self.LABEL_COLOR,
                          font=('Helvetica', 10, 'bold'))

        # --- scan points -------------------------------------------------- #
        # Bucket by sector so we can highlight the nearest hit per sector.
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

        # --- per-sector nearest labels ------------------------------------ #
        for ang_deg, d_m in sectors.values():
            rad = math.radians(ang_deg)
            x = cx + d_m * px_per_m * math.sin(rad)
            y = cy - d_m * px_per_m * math.cos(rad)
            c.create_oval(x - 3, y - 3, x + 3, y + 3,
                          fill=self.NEAR_COLOR, outline='')
            # Label sits a hair further out than the point.
            tx = cx + (d_m * px_per_m + 14) * math.sin(rad)
            ty = cy - (d_m * px_per_m + 14) * math.cos(rad)
            c.create_text(tx, ty, text=f'{d_m:.2f} m',
                          fill=self.LABEL_COLOR, font=('Helvetica', 9))

        # --- robot marker (triangle pointing forward / up) ---------------- #
        c.create_polygon(
            cx,       cy - 10,
            cx - 7,   cy + 7,
            cx + 7,   cy + 7,
            fill=self.ROBOT_COLOR, outline='',
        )

        # --- HUD: total points + max ring --------------------------------- #
        c.create_text(12, 10, anchor='nw',
                      text=f'{len(pts)} returns  •  range {max_r:.0f} m',
                      fill='#94a3b8', font=('Helvetica', 10))

        self.top.after(self.REDRAW_MS, self._redraw)

    def close(self):
        pass


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description='Pi desktop GUI for wifi mode (camera + lidar polar plot).')
    parser.add_argument('--no-camera', action='store_true', help='Skip the camera window.')
    parser.add_argument('--no-lidar',  action='store_true', help='Skip the lidar window.')
    parser.add_argument('--lidar-port', default='/dev/ttyUSB0',
                        help='Serial port for the RPLidar (default: /dev/ttyUSB0).')
    parser.add_argument('--camera-fps', type=int, default=5,
                        help='Live preview frame rate (default: 5).')
    parser.add_argument('--camera-width',  type=int, default=640)
    parser.add_argument('--camera-height', type=int, default=480)
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

    root = tk.Tk()
    root.withdraw()

    cam_win = None
    if not args.no_camera:
        cam_win = CameraWindow(root, camera, fps=args.camera_fps)
        cam_win.top.protocol('WM_DELETE_WINDOW', root.quit)

    lidar_win = None
    if not args.no_lidar:
        lidar_win = LidarWindow(root, lidar)
        lidar_win.top.protocol('WM_DELETE_WINDOW', root.quit)

    try:
        root.mainloop()
    finally:
        if cam_win:
            cam_win.close()
        if lidar_win:
            lidar_win.close()
        if lidar:
            lidar.stop()
        if camera:
            camera.close()


if __name__ == '__main__':
    main()
