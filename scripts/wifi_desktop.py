#!/usr/bin/env python3
"""Pi desktop GUI for WiFi-mode operation.

Spawns two top-level Tk windows on the Pi's desktop so the operator can
see what the robot sees while driving over WiFi from the same machine:

  1. "Robot Camera"  — live IMX519 preview at ~5 fps.
  2. "Robot Map"     — OpenStreetMap tiles via tkintermapview, with the
                       robot drawn at the GPS position (or a fixed
                       --lat / --lng pair) and a polygon overlay built
                       from the latest RPLidar scan showing obstacles
                       around the robot at real-world scale (zoom and
                       pan with the mouse, the lidar shape moves with
                       the tiles).

Hardware reuse: CameraSource and LidarSource are imported straight from
scripts/ble_server.py — there's only one camera and one lidar attached
to the Pi, so wifi-desktop and ble_server cannot run simultaneously
(scripts/run-wifi-desktop.sh handles that).

GPS: optional. If the `gps` Python package + a running gpsd are present,
the robot marker tracks the live fix. Otherwise the marker sits at the
--lat / --lng you pass (default: 0,0 — pass real coords to see the map
actually centred where you are).

Usage:
    python3 scripts/wifi_desktop.py                          # auto GPS
    python3 scripts/wifi_desktop.py --lat 40.7128 --lng -74  # fixed pos
    python3 scripts/wifi_desktop.py --no-lidar               # camera only
    python3 scripts/wifi_desktop.py --no-camera              # map only

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
from tkinter import ttk

# Bring in the same camera/lidar classes the BLE bridge uses.  This is
# the same script directory either way, so the import path is stable.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from ble_server import CameraSource, LidarSource  # type: ignore
except Exception as e:
    sys.exit(f'Could not import CameraSource/LidarSource from ble_server.py: {e}\n'
             'Make sure scripts/ble_server.py is present and unmodified.')


# 1° of latitude is ~111,320 m everywhere.  Same constant as the iOS
# MapModal — sub-cm error on the flat-earth approximation inside 100 m.
M_PER_DEG_LAT = 111320.0

# We treat lidar returns shorter than this as the chassis / specular
# noise, and longer than this as out of the A1's useful range.
LIDAR_MIN_M = 0.10
LIDAR_MAX_M = 12.0


# --------------------------------------------------------------------------- #
# GPS — optional
# --------------------------------------------------------------------------- #
class GpsSource:
    """Background thread that talks to gpsd via the `gps` Python module.

    No-op if either gpsd isn't running or the `gps` package isn't
    installed — the GUI just stays at the configured lat/lng.  Each fresh
    fix is delivered via the on_fix callback (called on this thread)."""

    def __init__(self, on_fix=None):
        self._on_fix = on_fix or (lambda _lat, _lng: None)
        self._stop = threading.Event()
        self._thread = None
        self._available = False
        try:
            import gps  # type: ignore
            self._gps = gps
        except ImportError:
            print('[desktop] gpsd python bindings not installed — using static lat/lng.',
                  file=sys.stderr)
            return
        except Exception as e:
            print(f'[desktop] gps import failed ({e}) — using static lat/lng.', file=sys.stderr)
            return
        self._available = True

    def start(self):
        if not self._available:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name='wifi-desktop-gps')
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            session = self._gps.gps(mode=self._gps.WATCH_ENABLE | self._gps.WATCH_NEWSTYLE)
        except Exception as e:
            print(f'[desktop] could not connect to gpsd: {e}', file=sys.stderr)
            return
        while not self._stop.is_set():
            try:
                report = session.next()
                if getattr(report, 'class', '') == 'TPV':
                    lat = getattr(report, 'lat', None)
                    lng = getattr(report, 'lon', None)
                    if lat is not None and lng is not None:
                        try:
                            self._on_fix(float(lat), float(lng))
                        except Exception:
                            pass
            except StopIteration:
                time.sleep(1)
            except Exception as e:
                print(f'[desktop] gps loop error: {e}', file=sys.stderr)
                time.sleep(2)


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
        self._camera = camera
        self._fps = max(1, min(15, int(fps)))
        self._stop = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=1)

        self.top = tk.Toplevel(root)
        self.top.title('Robot Camera')
        self.top.configure(bg='#0f172a')
        self.top.geometry('640x520')

        self._label_status = tk.Label(
            self.top, text='', fg='#94a3b8', bg='#0f172a',
            font=('Helvetica', 11), anchor='w', padx=10, pady=6,
        )
        self._label_status.pack(side='top', fill='x')

        self._label_image = tk.Label(self.top, bg='#020617')
        self._label_image.pack(side='top', fill='both', expand=True, padx=8, pady=(0, 8))

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
                # Latest-wins: replace any frame already queued so the
                # GUI never falls behind the camera.
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
                # Resize to the label's current size so the picture fills
                # the panel; on first draw the label may not be laid out
                # yet so guard for that.
                w = max(160, self._label_image.winfo_width())
                h = max(120, self._label_image.winfo_height())
                # Keep aspect ratio.
                src_w, src_h = img.size
                scale = min(w / src_w, h / src_h)
                tw, th = max(1, int(src_w * scale)), max(1, int(src_h * scale))
                img = img.resize((tw, th), self._Image.BILINEAR)
                self._photo = self._ImageTk.PhotoImage(img)
                self._label_image.config(image=self._photo)
            except Exception as e:
                self._set_status(f'Display error: {e}')
        self.top.after(50, self._drain)

    def close(self):
        self._stop.set()


# --------------------------------------------------------------------------- #
# Map window
# --------------------------------------------------------------------------- #
class MapWindow:
    """Tk top-level showing an OSM map with a robot marker + lidar polygon.

    Uses tkintermapview — a third-party widget that handles tile fetching
    and pan/zoom for us, so we only have to compute lat/lng for each
    lidar point and hand it the polygon.  Tile server is OSM by default;
    we set a polite User-Agent."""

    def __init__(self, root: tk.Tk, lat: float, lng: float, lidar: LidarSource | None):
        self._lat = lat
        self._lng = lng
        self._latest_points = []          # list[(angle_deg, distance_mm)]
        self._scan_lock = threading.Lock()

        self.top = tk.Toplevel(root)
        self.top.title('Robot Map')
        self.top.configure(bg='#0f172a')
        self.top.geometry('720x640')

        self._status = tk.Label(
            self.top, text='', fg='#94a3b8', bg='#0f172a',
            font=('Helvetica', 11), anchor='w', padx=10, pady=6,
        )
        self._status.pack(side='top', fill='x')

        try:
            import tkintermapview  # type: ignore
            self._mapview = tkintermapview.TkinterMapView(
                self.top, width=720, height=580, corner_radius=0,
            )
            # OSM is the default, but pin it explicitly + set a UA per their tile policy.
            self._mapview.set_tile_server(
                'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
                max_zoom=19,
            )
            self._mapview.pack(side='top', fill='both', expand=True)
            self._mapview.set_position(lat, lng)
            self._mapview.set_zoom(18)
            self._marker = self._mapview.set_marker(lat, lng, text='Robot')
            self._polygon = None
            self._status.config(text=f'Centred on {lat:.5f}, {lng:.5f}. Pan + zoom with the mouse.')
        except ImportError:
            self._mapview = None
            self._status.config(
                text='tkintermapview not installed. Run: pip3 install --break-system-packages tkintermapview')
        except Exception as e:
            self._mapview = None
            self._status.config(text=f'Map widget failed: {e}')

        # Wire lidar in.
        if lidar is not None and lidar.available:
            lidar._on_scan = self._on_scan   # noqa: SLF001 — same hook the BLE streamer uses
            lidar.start()
            self.top.after(100, self._refresh_polygon)
        else:
            note = lidar.reason if lidar is not None else 'disabled'
            self._status.config(text=f'{self._status.cget("text")}  •  Lidar: {note}')

    def _on_scan(self, points):
        # Called on the rplidar thread — just stash; rendering happens on
        # the Tk main loop via _refresh_polygon().
        with self._scan_lock:
            self._latest_points = list(points)

    def update_position(self, lat, lng):
        self._lat, self._lng = lat, lng
        if self._mapview is not None:
            try:
                self._marker.set_position(lat, lng)
            except Exception:
                pass

    def _refresh_polygon(self):
        if self._mapview is None:
            return
        with self._scan_lock:
            pts = self._latest_points
        if pts:
            coords = self._project(pts)
            if len(coords) >= 3:
                try:
                    if self._polygon is None:
                        self._polygon = self._mapview.set_polygon(
                            coords,
                            fill_color=None,
                            outline_color='#38bdf8',
                            border_width=2,
                            name='lidar',
                        )
                    else:
                        # tkintermapview doesn't expose update_position
                        # for polygons in all versions, so delete+recreate
                        # is the portable path.
                        try:
                            self._polygon.delete()
                        except Exception:
                            pass
                        self._polygon = self._mapview.set_polygon(
                            coords,
                            fill_color=None,
                            outline_color='#38bdf8',
                            border_width=2,
                            name='lidar',
                        )
                except Exception as e:
                    self._status.config(text=f'Polygon update failed: {e}')
        # ~5 Hz polygon repaint — matches the typical RPLidar scan rate.
        self.top.after(200, self._refresh_polygon)

    def _project(self, points):
        """Convert lidar (angle_deg, distance_mm) → list of (lat, lng)
        around the current robot position, sorted by angle so the polygon
        traces a coherent outline."""
        lat, lng = self._lat, self._lng
        cos_lat = math.cos(math.radians(lat))
        if abs(cos_lat) < 1e-9:
            cos_lat = 1e-9
        usable = []
        for ang, dist_mm in points:
            d_m = dist_mm / 1000.0
            if d_m < LIDAR_MIN_M or d_m > LIDAR_MAX_M:
                continue
            usable.append((float(ang) % 360.0, d_m))
        usable.sort(key=lambda p: p[0])
        out = []
        for ang_deg, d_m in usable:
            rad = math.radians(ang_deg)
            d_north = d_m * math.cos(rad)
            d_east  = d_m * math.sin(rad)
            out.append((
                lat + d_north / M_PER_DEG_LAT,
                lng + d_east  / (M_PER_DEG_LAT * cos_lat),
            ))
        return out


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(
        description='Pi desktop GUI for wifi mode (camera + lidar map).')
    parser.add_argument('--lat', type=float, default=None,
                        help='Robot latitude. Required unless gpsd is running.')
    parser.add_argument('--lng', type=float, default=None,
                        help='Robot longitude. Required unless gpsd is running.')
    parser.add_argument('--no-camera', action='store_true', help='Skip the camera window.')
    parser.add_argument('--no-lidar',  action='store_true', help='Skip the lidar overlay.')
    parser.add_argument('--lidar-port', default='/dev/ttyUSB0',
                        help='Serial port for the RPLidar (default: /dev/ttyUSB0).')
    parser.add_argument('--camera-fps', type=int, default=5,
                        help='Live preview frame rate (default: 5).')
    parser.add_argument('--camera-width',  type=int, default=640)
    parser.add_argument('--camera-height', type=int, default=480)
    args = parser.parse_args()

    # Open the camera first so its 1.5 s warm-up overlaps with the GUI
    # startup instead of stalling it.
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

    # Default to (0,0) if no GPS hardware and no --lat/--lng — the user
    # at least sees an OSM rendering of the prime meridian rather than
    # crashing.  GpsSource will overwrite this once a fix arrives.
    lat = args.lat if args.lat is not None else 0.0
    lng = args.lng if args.lng is not None else 0.0
    if args.lat is None or args.lng is None:
        print('[desktop] no --lat/--lng provided; will try gpsd, otherwise centre on (0,0).',
              file=sys.stderr)

    root = tk.Tk()
    # Hide the root — we use Toplevels for the actual windows.
    root.withdraw()

    cam_win = None
    if not args.no_camera:
        cam_win = CameraWindow(root, camera, fps=args.camera_fps)
        cam_win.top.protocol('WM_DELETE_WINDOW', root.quit)

    map_win = MapWindow(root, lat, lng, lidar)
    map_win.top.protocol('WM_DELETE_WINDOW', root.quit)

    # GPS — non-blocking; if not available, the marker stays put.
    def _on_fix(lat, lng):
        # Tk widgets must be touched on the main thread.  Schedule the
        # marker update via after_idle.
        root.after_idle(lambda: map_win.update_position(lat, lng))
    gps = GpsSource(on_fix=_on_fix)
    gps.start()

    try:
        root.mainloop()
    finally:
        gps.stop()
        if cam_win:
            cam_win.close()
        if lidar:
            lidar.stop()
        if camera:
            camera.close()


if __name__ == '__main__':
    main()
