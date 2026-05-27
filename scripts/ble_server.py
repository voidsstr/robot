#!/usr/bin/env python3
"""BLE -> Arduino command bridge + live video / photo streamer.

Exposes a BLE GATT peripheral (Nordic UART Service, plus a custom video
notify characteristic) that an iPhone or Android phone can pair with.
Movement commands are parsed and the matching single-character control
byte is written directly to the Arduino over USB serial. Live video from
the Pi camera is chunked and pushed over the video characteristic; the
PHOTO command captures a higher-resolution still and pushes it back the
same way with the photo flag set.

GATT layout (Nordic UART Service — supported out of the box by nRF Connect,
Adafruit Bluefruit Connect, LightBlue, etc.):

  Service  6E400001-B5A3-F393-E0A9-E50E24DCCA9E
    RX     6E400002-B5A3-F393-E0A9-E50E24DCCA9E   Write    phone -> robot (commands)
    TX     6E400003-B5A3-F393-E0A9-E50E24DCCA9E   Notify   robot -> phone (replies)
    VIDEO  6E400004-B5A3-F393-E0A9-E50E24DCCA9E   Notify   robot -> phone (video + photo)

Video / photo wire format — each BLE notification carries one chunk:

  byte 0   FRAME_ID    rolling counter (0–255), identifies the frame
  byte 1   CHUNK_IDX   0-based index of this chunk within the frame
  byte 2   TOTAL       total number of chunks for this frame (1–255)
  byte 3   FLAGS       bit 0 = 1 if this is a high-res "photo" response
  bytes 4… JPEG_DATA   chunk of the JPEG file, in order

This must match ios-app/src/lib/videoFrames.ts.

Accepted text commands (written to the RX char, newline optional):
  UP / FORWARD / W / ACC / ACCELERATE       -> 'U'
  DOWN / BACK / S / DEC / DECELERATE        -> 'D'
  LEFT / A                                  -> 'L'
  RIGHT / D                                 -> 'R'
  STOP / SPACE / X                          -> 'S'
  STATUS                                    -> serial + camera + stream + lidar state
  PHOTO                                     -> capture + push a single high-res JPEG
  VQ:<n>                                    -> set live JPEG quality (1-95)
  VFPS:<n>                                  -> set live frame rate (1-15)
  VRES:<w>x<h>                              -> reconfigure camera to <w>x<h> (brief gap)
  VOFF / VON                                -> pause / resume the live video stream
  CAM:<n>                                   -> switch to camera port <n> (Pi 5: 0 or 1)
  CAMINFO                                   -> reply with all detected cameras
  LIDAR:ON / LIDAR:OFF                      -> start / stop the RPLidar scan loop

A short reply (e.g. `OK: UP`) is pushed back as a TX notification.

Requirements:  BlueZ >= 5.50, python3-dbus, python3-gi, `bluezero`,
  `pyserial`, and (for video / photo) `python3-picamera2`. Without
  picamera2 the movement commands still work; the camera features
  are simply disabled.

Run as root (BlueZ D-Bus + advertising usually needs it):
  sudo python3 scripts/ble_server.py
  sudo python3 scripts/ble_server.py --name MyRobot --serial /dev/ttyACM0
  sudo python3 scripts/ble_server.py --no-camera          # commands only
"""

import argparse
import io
import sys
import threading
import time

NUS_SERVICE    = '6E400001-B5A3-F393-E0A9-E50E24DCCA9E'
NUS_RX_CHAR    = '6E400002-B5A3-F393-E0A9-E50E24DCCA9E'   # Write   (phone -> robot)
NUS_TX_CHAR    = '6E400003-B5A3-F393-E0A9-E50E24DCCA9E'   # Notify  (robot -> phone)
NUS_VIDEO_CHAR = '6E400004-B5A3-F393-E0A9-E50E24DCCA9E'   # Notify  (robot -> phone, video)
NUS_LIDAR_CHAR = '6E400005-B5A3-F393-E0A9-E50E24DCCA9E'   # Notify  (robot -> phone, lidar)

# Path our auto-accept BlueZ agent registers under.  Any unused path works;
# it just needs to be unique and stable for the lifetime of the script.
AGENT_PATH = '/com/voidsstr/robot/agent'
BLUEZ_AGENT_IFACE = 'org.bluez.Agent1'

DEFAULT_NAME = 'RobotBLE'
DEFAULT_SERIAL_PORT = '/dev/ttyACM0'
DEFAULT_SERIAL_BAUD = 115200

# Video defaults — tuned for BLE bandwidth (~50–500 kbps practical depending on
# phone + radio environment). 320x240 @ Q60 lands around 6–10 KB per frame,
# i.e. ~30–55 chunks at 180 B payload. ~4 fps is a reasonable target.
DEFAULT_VIDEO_FPS = 4
DEFAULT_VIDEO_SIZE = (320, 240)
DEFAULT_VIDEO_QUALITY = 60
DEFAULT_PHOTO_SIZE = (1280, 960)
DEFAULT_PHOTO_QUALITY = 85

# Each BLE notification: 4 B header + CHUNK_PAYLOAD B data. iOS commonly
# negotiates ATT MTU around 185 (payload 182), so 180 + 4 = 184 fits.
# Bumping this requires the phone to actually negotiate a larger MTU.
CHUNK_PAYLOAD = 180

FLAG_PHOTO = 0x01

# Pacing between successive chunks on the GLib main loop. Too short and we
# starve other BLE callbacks; too long and frame rate falls. 5 ms gives
# ~200 chunks/s which is ahead of what BLE actually pushes anyway.
CHUNK_PACE_MS = 5

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
        last_err = None
        with self._lock:
            for _ in range(2):
                try:
                    if self._ser is None:
                        self._open_locked()
                    self._ser.write(payload)
                    self._ser.flush()
                    return True
                except Exception as e:
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


class CameraSource:
    """Picamera2 wrapper that can supply low-res video frames and high-res
    photos as JPEG bytes. If picamera2 isn't installed, every method is a
    no-op and `available` is False — the rest of the server still works.

    Designed for the Arducam IMX519 attached via the CSI ribbon, but works
    with any libcamera-supported sensor (the Pi camera v2/v3 too).

    On the Pi 5 there are two CSI camera ports (CAM0 / CAM1).  libcamera
    enumerates them as separate camera devices; we pick which one
    Picamera2 opens with `camera_num`.  None = let picamera2 use its
    default (typically the lowest-numbered detected camera).  At init we
    log the full Picamera2.global_camera_info() dump to stderr so it's
    obvious which port the connected sensor came up on — useful when you
    plug the ribbon into the wrong port and nothing appears.

    Video quality, fps target, and resolution can all be changed at
    runtime via set_*().  Quality and fps are cheap (next frame picks up
    the new value); resolution requires a stop/configure/start cycle that
    takes ~1 second.  Camera port is the same — set_camera_num() bounces
    the stream and re-opens against the new sensor."""

    def __init__(self, video_size, photo_size, video_quality, photo_quality,
                 camera_num=None):
        self._picam = None
        self._video_size = tuple(video_size)
        self._photo_size = tuple(photo_size)
        self._video_quality = int(video_quality)
        self._photo_quality = int(photo_quality)
        self._camera_num = None if camera_num is None else int(camera_num)
        self._last_error = ''
        self._cameras = []
        self._capture_lock = threading.Lock()

        try:
            from picamera2 import Picamera2  # type: ignore
        except ImportError:
            self._last_error = 'picamera2 not installed (sudo apt-get install python3-picamera2)'
            print('[ble] picamera2 not installed — video + photo disabled.', file=sys.stderr)
            print('      Install: sudo apt-get install python3-picamera2', file=sys.stderr)
            return
        except Exception as e:
            self._last_error = f'picamera2 import failed: {e}'
            print(f'[ble] picamera2 import failed ({e}); video + photo disabled.', file=sys.stderr)
            return

        self._Picamera2 = Picamera2

        # Verbose enumeration up front — every field libcamera knows about
        # each detected sensor goes to stderr.  This is the single biggest
        # debug lever: if the ribbon's on the wrong port, the dict for
        # that port is simply missing here, regardless of dtoverlay.
        try:
            cams = Picamera2.global_camera_info()
            self._cameras = list(cams) if cams else []
            if not self._cameras:
                print('[ble] CAMERA SCAN: libcamera reports ZERO cameras attached.', file=sys.stderr)
                print('      Pi 5 has two CSI ports (CAM0/CAM1).  Check:', file=sys.stderr)
                print('        - ribbon fully seated on BOTH ends, blue tab toward USB', file=sys.stderr)
                print('        - dtoverlay=imx519 (or your sensor) in /boot/firmware/config.txt', file=sys.stderr)
                print('        - `rpicam-hello --list-cameras` agrees', file=sys.stderr)
            else:
                print(f'[ble] CAMERA SCAN: {len(self._cameras)} camera(s) detected by libcamera:',
                      file=sys.stderr)
                for i, info in enumerate(self._cameras):
                    print(f'  [{i}] {info}', file=sys.stderr)
        except Exception as e:
            print(f'[ble] CAMERA SCAN failed: {e}', file=sys.stderr)
            self._cameras = []

        try:
            self._open_locked()
            print(f'[ble] camera ready on port {self._camera_num if self._camera_num is not None else "(default)"} '
                  f'at {self._video_size} (video) / {self._photo_size} (photo).',
                  file=sys.stderr)
        except Exception as e:
            self._last_error = f'open failed: {e}'
            print(f'[ble] camera init failed ({e}); video + photo disabled.', file=sys.stderr)
            print('      Check: rpicam-hello works? dtoverlay=imx519 in /boot/firmware/config.txt?',
                  file=sys.stderr)
            print('      Try toggling ports from the app (CAM:0 / CAM:1).', file=sys.stderr)
            self._picam = None

    def _open_locked(self):
        """(Re-)configure picamera2 at self._video_size. Caller holds the lock."""
        if self._picam is not None:
            try: self._picam.stop()
            except Exception: pass
            try: self._picam.close()
            except Exception: pass
            self._picam = None
        if self._camera_num is None:
            picam = self._Picamera2()
        else:
            picam = self._Picamera2(camera_num=self._camera_num)
        cfg = picam.create_video_configuration(main={'size': self._video_size, 'format': 'RGB888'})
        picam.configure(cfg)
        picam.start()
        time.sleep(1.5)  # let AE/AWB settle before the first frame goes out
        self._picam = picam
        self._last_error = ''

    @property
    def available(self):
        return self._picam is not None

    @property
    def last_error(self):
        return self._last_error

    @property
    def cameras(self):
        """List of dicts as returned by Picamera2.global_camera_info() at
        construction time.  Each dict has at least 'Model' and 'Num'
        keys; on Pi 5 also 'Location' (which CSI port: 0 / 1)."""
        return list(self._cameras)

    @property
    def state(self):
        return {
            'available': self.available,
            'video_size': self._video_size,
            'photo_size': self._photo_size,
            'video_quality': self._video_quality,
            'photo_quality': self._photo_quality,
            'camera_num': self._camera_num,
            'detected': len(self._cameras),
            'last_error': self._last_error,
        }

    def set_video_quality(self, q):
        q = max(1, min(95, int(q)))
        self._video_quality = q
        return q

    def set_video_size(self, w, h):
        """Reconfigure picamera2 to a new live-stream resolution.  Briefly
        interrupts the stream (stop → configure → start; ~1 s)."""
        w = max(64, min(1920, int(w)))
        h = max(48,  min(1080, int(h)))
        with self._capture_lock:
            self._video_size = (w, h)
            if self._picam is None:
                return self._video_size
            try:
                self._open_locked()
            except Exception as e:
                print(f'[ble] camera reconfigure to {w}x{h} failed: {e}', file=sys.stderr)
                self._picam = None
        return self._video_size

    def set_camera_num(self, n):
        """Switch which CSI port libcamera opens.  Bounces the stream:
        stops the current sensor, opens the new one, and restarts the
        configured video pipeline.  Re-raises on failure so the BLE layer
        can report ERR back to the app and the user knows the toggle
        didn't take.  On success the new port is sticky for subsequent
        capture / reconfigure calls.

        Re-scans global_camera_info() each time — that's how a freshly
        plugged-in sensor on the other port becomes visible without
        restarting the whole BLE bridge."""
        n = int(n)
        with self._capture_lock:
            # Refresh the enumeration first so the new port is picked up
            # even if it was empty at startup.
            try:
                cams = self._Picamera2.global_camera_info()
                self._cameras = list(cams) if cams else []
                print(f'[ble] CAMERA RESCAN before switching to port {n}: {len(self._cameras)} detected:',
                      file=sys.stderr)
                for i, info in enumerate(self._cameras):
                    print(f'  [{i}] {info}', file=sys.stderr)
            except Exception as e:
                print(f'[ble] camera rescan failed: {e}', file=sys.stderr)

            prev = self._camera_num
            self._camera_num = n
            try:
                self._open_locked()
                print(f'[ble] switched to camera port {n}.', file=sys.stderr)
                return n
            except Exception as e:
                # Roll back so we don't leave _camera_num pointing at a
                # port we couldn't open.  The previous port may or may
                # not still be available; try to restore.
                self._last_error = f'CAM:{n} open failed: {e}'
                print(f'[ble] camera port {n} failed ({e}); rolling back to {prev}.', file=sys.stderr)
                self._camera_num = prev
                try:
                    self._open_locked()
                except Exception as e2:
                    print(f'[ble] rollback also failed ({e2}); camera offline.', file=sys.stderr)
                    self._picam = None
                raise

    def capture_video_jpeg(self):
        """Capture one low-res frame and return JPEG bytes (or None)."""
        if not self._picam:
            return None
        with self._capture_lock:
            if not self._picam:
                return None
            try:
                self._picam.options['quality'] = self._video_quality
                buf = io.BytesIO()
                self._picam.capture_file(buf, format='jpeg')
                return buf.getvalue()
            except Exception as e:
                print(f'[ble] video capture failed: {e}', file=sys.stderr)
                return None

    def capture_video_rgb(self):
        """Capture one frame as a height×width×3 uint8 numpy array (RGB888).
        Used by the on-Pi desktop GUI to avoid a JPEG encode/decode round
        trip when displaying frames locally."""
        if not self._picam:
            return None
        with self._capture_lock:
            if not self._picam:
                return None
            try:
                return self._picam.capture_array('main')
            except Exception as e:
                print(f'[ble] rgb capture failed: {e}', file=sys.stderr)
                return None

    def capture_photo_jpeg(self):
        """Switch to the still configuration, capture one frame, switch back."""
        if not self._picam:
            return None
        with self._capture_lock:
            if not self._picam:
                return None
            try:
                still_cfg = self._picam.create_still_configuration(
                    main={'size': self._photo_size, 'format': 'RGB888'}
                )
                self._picam.options['quality'] = self._photo_quality
                buf = io.BytesIO()
                self._picam.switch_mode_and_capture_file(still_cfg, buf, format='jpeg')
                return buf.getvalue()
            except Exception as e:
                print(f'[ble] photo capture failed: {e}', file=sys.stderr)
                return None

    def close(self):
        if self._picam:
            try:
                self._picam.stop()
                self._picam.close()
            except Exception:
                pass
            self._picam = None


class VideoStreamer:
    """Chunks JPEG frames and pushes them over the video characteristic.

    Runs entirely on the GLib main loop — bluezero's D-Bus signalling
    isn't safe to call from arbitrary worker threads, so chunk-by-chunk
    transmission is scheduled via GLib.timeout_add. While a frame is
    being flushed, any new live-video frames are dropped (latest wins).
    PHOTO frames are queued and sent as soon as the current frame
    finishes."""

    def __init__(self, video_char):
        self._video_char = video_char
        self._frame_id = 0
        self._sending = False
        self._pending_photo = None  # JPEG bytes waiting to go out after current frame
        # Diagnostics — surfaced via STATUS so the user can tell at a glance
        # whether the BLE link is bandwidth-bound vs camera-bound.
        self._frames_sent = 0
        self._frames_dropped = 0
        self._photos_sent = 0
        # GLib is imported lazily so --help works without gi.
        from gi.repository import GLib  # type: ignore
        self._GLib = GLib

    @property
    def stats(self):
        return {'sent': self._frames_sent, 'dropped': self._frames_dropped,
                'photos': self._photos_sent}

    def _next_frame_id(self):
        fid = self._frame_id
        self._frame_id = (self._frame_id + 1) & 0xFF
        return fid

    def _make_chunks(self, frame_id, jpeg_bytes, is_photo):
        flags = FLAG_PHOTO if is_photo else 0
        total = (len(jpeg_bytes) + CHUNK_PAYLOAD - 1) // CHUNK_PAYLOAD
        total = max(1, min(total, 255))
        for idx in range(total):
            start = idx * CHUNK_PAYLOAD
            chunk = jpeg_bytes[start:start + CHUNK_PAYLOAD]
            yield bytes([frame_id, idx, total, flags]) + chunk

    def push_video_frame(self, jpeg_bytes):
        """Try to enqueue a live-video frame. Dropped if a frame is in flight."""
        if self._sending:
            self._frames_dropped += 1
            return False
        self._frames_sent += 1
        self._begin_send(jpeg_bytes, is_photo=False)
        return True

    def push_photo(self, jpeg_bytes):
        """Send a high-res photo frame. Queued behind an in-flight frame
        so its chunks aren't interleaved."""
        self._photos_sent += 1
        if self._sending:
            self._pending_photo = jpeg_bytes
            return
        self._begin_send(jpeg_bytes, is_photo=True)

    def _begin_send(self, jpeg_bytes, is_photo):
        self._sending = True
        chunks_iter = self._make_chunks(self._next_frame_id(), jpeg_bytes, is_photo)

        def push_next():
            chunk = next(chunks_iter, None)
            if chunk is None:
                self._sending = False
                # If a photo was queued while we were flushing video, send it now.
                if self._pending_photo is not None:
                    pending, self._pending_photo = self._pending_photo, None
                    self._begin_send(pending, is_photo=True)
                return False  # stop timeout
            try:
                self._video_char.set_value(list(chunk))
            except Exception as e:
                print(f'[ble] video set_value failed: {e}', file=sys.stderr)
                self._sending = False
                return False
            return True  # keep timeout firing

        self._GLib.timeout_add(CHUNK_PACE_MS, push_next)


# ============================================================================
# Lidar
# ============================================================================
#
# We assume a Slamtec RPLidar A1/A2/A3 over USB serial (/dev/ttyUSB0 by
# default) — the most common hobby 360° lidar.  The `rplidar` Python
# library does the protocol work.  If the device or library isn't
# present, every method is a no-op and `available` is False; the rest of
# the server still works as before.

DEFAULT_LIDAR_PORT = '/dev/ttyUSB0'
DEFAULT_LIDAR_BAUD = 115200

# Wire format for one lidar scan chunk (must mirror ios-app/src/lib/lidarFrames.ts):
#
#   byte 0   SCAN_ID     rolling counter, identifies the 360° scan
#   byte 1   CHUNK_IDX   0-based index within the scan
#   byte 2   TOTAL       total chunks for the scan
#   byte 3   FLAGS       reserved (must be 0)
#   bytes 4… POINTS      N × 4-byte points
#       uint16 little-endian: angle in centi-degrees (0..35999)
#       uint16 little-endian: distance in millimeters (0..65535, 0 = no return)
#
# 4 bytes per point × 44 points = 176 bytes payload, +4 byte header = 180 B.
# A 360-point scan therefore goes out in ceil(360/44) = 9 chunks.
LIDAR_POINTS_PER_CHUNK = 44
LIDAR_CHUNK_HEADER_LEN = 4


class LidarSource:
    """Background thread that drives an RPLidar and publishes the latest
    completed scan via on_scan(points_iterable).

    points: iterable of (angle_deg: float, distance_mm: int) tuples.
    The thread coalesces RPLidar's stream of partial scans into
    once-per-revolution full scans; consumers see ~5–10 scans/sec at A1
    default settings."""

    def __init__(self, port=DEFAULT_LIDAR_PORT, on_scan=None):
        self._port = port
        self._on_scan = on_scan or (lambda _pts: None)
        self._stop = threading.Event()
        self._thread = None
        self._lidar = None
        self._available = False
        self._reason = ''
        self._RPLidar = None
        self._fatal = False    # True once a permanent failure (e.g. lib missing) makes re-probing pointless

        try:
            # `rplidar` (Roboticia fork) is the most common pip name.  We
            # import here so install_deps doesn't have to be present for
            # --help / camera-only usage.
            from rplidar import RPLidar  # type: ignore
            self._RPLidar = RPLidar
        except ImportError:
            self._reason = 'rplidar Python package not installed (pip3 install rplidar)'
            self._fatal = True
            print(f'[ble] {self._reason} — lidar disabled.', file=sys.stderr)
            return
        except Exception as e:
            self._reason = f'rplidar import failed: {e}'
            self._fatal = True
            print(f'[ble] {self._reason} — lidar disabled.', file=sys.stderr)
            return

        # First probe.  Failures here aren't fatal — the device could be
        # plugged in later and a subsequent LIDAR:ON will call reprobe().
        self.reprobe(quiet=False)

    def reprobe(self, quiet=True):
        """Re-check whether the lidar device is plug-in-able right now.
        Idempotent and safe to call repeatedly.  Returns the new value of
        `available`.  Skipped if we hit a fatal error (missing library) —
        that won't fix itself without restarting the process.

        quiet=True suppresses success/failure prints; we keep the very
        first probe loud so startup logs show the lidar state."""
        if self._fatal:
            return False
        # If we're already scanning, the device is plainly there — no need
        # to redo the touch test, which would race with the rplidar lib.
        if self._available and self._thread is not None:
            return True
        try:
            import os
            if not os.path.exists(self._port):
                self._available = False
                self._reason = f'{self._port} not present — plug the lidar in, or pass --lidar-port'
                if not quiet:
                    print(f'[ble] {self._reason}; lidar disabled.', file=sys.stderr)
                return False
            if not os.access(self._port, os.R_OK | os.W_OK):
                self._available = False
                self._reason = f'no read/write on {self._port} — add user to dialout group'
                if not quiet:
                    print(f'[ble] {self._reason}; lidar disabled.', file=sys.stderr)
                return False
            # Recovered from a previous "not plugged in"?  Announce it.
            if not self._available:
                print(f'[ble] lidar ready on {self._port}.', file=sys.stderr)
            self._available = True
            self._reason = ''
            return True
        except Exception as e:
            self._available = False
            self._reason = f'lidar probe failed: {e}'
            if not quiet:
                print(f'[ble] {self._reason}; lidar disabled.', file=sys.stderr)
            return False

    @property
    def available(self):
        return self._available

    @property
    def reason(self):
        return self._reason

    def start(self):
        # Always re-probe before starting so we pick up a device that
        # was plugged in after ble_server.py launched.
        self.reprobe()
        if not self._available or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name='ble-lidar')
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._lidar is not None:
            try: self._lidar.stop()
            except Exception: pass
            try: self._lidar.stop_motor()
            except Exception: pass
            try: self._lidar.disconnect()
            except Exception: pass
            self._lidar = None
        self._thread = None

    def _run(self):
        try:
            self._lidar = self._RPLidar(self._port, baudrate=DEFAULT_LIDAR_BAUD, timeout=3)
            # iter_scans yields lists of (quality, angle, distance) once
            # per revolution.  min_len lower = more scans/sec but each
            # scan is sparser; 100 is a reasonable floor for A1.
            for scan in self._lidar.iter_scans(max_buf_meas=2000, min_len=100):
                if self._stop.is_set():
                    break
                pts = [(float(angle), int(dist)) for (_q, angle, dist) in scan if dist > 0]
                try:
                    self._on_scan(pts)
                except Exception as e:
                    print(f'[ble] lidar on_scan callback error: {e}', file=sys.stderr)
        except Exception as e:
            print(f'[ble] lidar thread exited: {e}', file=sys.stderr)
            self._available = False
            self._reason = str(e)
        finally:
            try:
                if self._lidar is not None:
                    self._lidar.stop(); self._lidar.stop_motor(); self._lidar.disconnect()
            except Exception:
                pass
            self._lidar = None


class LidarStreamer:
    """Same chunk-on-the-GLib-loop pattern as VideoStreamer, but for lidar
    scans on their own characteristic.  Drops new scans while the previous
    one is still flushing (latest-wins) — lidar is best-effort like video."""

    def __init__(self, lidar_char):
        self._lidar_char = lidar_char
        self._scan_id = 0
        self._sending = False
        self._scans_sent = 0
        self._scans_dropped = 0
        from gi.repository import GLib  # type: ignore
        self._GLib = GLib

    @property
    def stats(self):
        return {'sent': self._scans_sent, 'dropped': self._scans_dropped}

    def _next_scan_id(self):
        sid = self._scan_id
        self._scan_id = (self._scan_id + 1) & 0xFF
        return sid

    @staticmethod
    def _pack_points(points):
        """Pack [(angle_deg, distance_mm)] into a binary blob using the
        wire format documented above."""
        import struct
        out = bytearray()
        for ang, dist in points:
            cdeg = int(round(ang * 100.0)) % 36000
            if cdeg < 0: cdeg += 36000
            dmm = max(0, min(65535, int(dist)))
            out += struct.pack('<HH', cdeg, dmm)
        return bytes(out)

    def push_scan(self, points):
        if self._sending:
            self._scans_dropped += 1
            return False
        blob = self._pack_points(points)
        if not blob:
            return False
        total_points = len(blob) // 4
        # Cap at 255 chunks (header field is 1 byte).  At 44 pts/chunk
        # that's 11220 points/scan — way more than any RPLidar produces.
        chunks_needed = (total_points + LIDAR_POINTS_PER_CHUNK - 1) // LIDAR_POINTS_PER_CHUNK
        if chunks_needed > 255:
            blob = blob[: 255 * LIDAR_POINTS_PER_CHUNK * 4]
            chunks_needed = 255
        scan_id = self._next_scan_id()
        chunks = []
        for idx in range(chunks_needed):
            start = idx * LIDAR_POINTS_PER_CHUNK * 4
            payload = blob[start: start + LIDAR_POINTS_PER_CHUNK * 4]
            chunks.append(bytes([scan_id, idx, chunks_needed, 0]) + payload)

        self._sending = True
        self._scans_sent += 1
        it = iter(chunks)

        def push_next():
            chunk = next(it, None)
            if chunk is None:
                self._sending = False
                return False
            try:
                self._lidar_char.set_value(list(chunk))
            except Exception as e:
                print(f'[ble] lidar set_value failed: {e}', file=sys.stderr)
                self._sending = False
                return False
            return True

        self._GLib.timeout_add(CHUNK_PACE_MS, push_next)
        return True


def main():
    parser = argparse.ArgumentParser(description='BLE -> Arduino command bridge + video stream')
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
    parser.add_argument('--no-camera', action='store_true', help='Disable video/photo (commands only)')
    parser.add_argument('--camera-num', type=int, default=None,
                        help='Which CSI port to open at startup (Pi 5: 0 or 1).  Omit to let '
                             'libcamera pick the default.  The app can also switch live with CAM:n.')
    parser.add_argument('--video-fps', type=int, default=DEFAULT_VIDEO_FPS, help='Live video frame rate')
    parser.add_argument('--video-width', type=int, default=DEFAULT_VIDEO_SIZE[0])
    parser.add_argument('--video-height', type=int, default=DEFAULT_VIDEO_SIZE[1])
    parser.add_argument('--photo-width', type=int, default=DEFAULT_PHOTO_SIZE[0])
    parser.add_argument('--photo-height', type=int, default=DEFAULT_PHOTO_SIZE[1])
    parser.add_argument('--no-lidar', action='store_true',
                        help='Disable the RPLidar even if the device is present.')
    parser.add_argument('--lidar-port', default=DEFAULT_LIDAR_PORT,
                        help=f'Serial port for the RPLidar (default: {DEFAULT_LIDAR_PORT}).')
    parser.add_argument('--lidar-autostart', action='store_true',
                        help='Start scanning at boot instead of waiting for the app '
                             "to send LIDAR:ON.  Useful for SLAM-style use cases where "
                             "you want a continuous environment map.")
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

    try:
        from gi.repository import GLib  # type: ignore
    except ImportError:
        sys.exit('python3-gi is not installed. Run: sudo apt-get install python3-gi')

    adapter_addr = args.adapter
    if adapter_addr is None:
        adapters = list(adapter.Adapter.available())
        if not adapters:
            sys.exit('No Bluetooth adapter found. Try: sudo bluetoothctl power on')
        adapter_addr = adapters[0].address

    link = ArduinoLink(args.serial, args.baud)
    camera = (CameraSource(
        video_size=(args.video_width, args.video_height),
        photo_size=(args.photo_width, args.photo_height),
        video_quality=DEFAULT_VIDEO_QUALITY,
        photo_quality=DEFAULT_PHOTO_QUALITY,
        camera_num=args.camera_num,
    ) if not args.no_camera else None)
    lidar = LidarSource(port=args.lidar_port) if not args.no_lidar else None

    robot = peripheral.Peripheral(adapter_addr, local_name=args.name)
    robot.add_service(srv_id=1, uuid=NUS_SERVICE, primary=True)

    # Forward decls; assigned after the TX / VIDEO / LIDAR characteristics are created.
    push_reply = lambda _line: None
    streamer = None
    lidar_streamer = None

    # Forward-declared so handle_command can call it before we instantiate
    # the watchdog further down.  Re-assigned at startup if --watchdog > 0.
    feed_watchdog = lambda: None
    set_video_fps = lambda _fps: None        # re-assigned once _start_video exists
    pause_video   = lambda: None
    resume_video  = lambda: None

    def handle_command(text):
        cmd = text.strip().upper()
        if not cmd:
            return
        if cmd in ('QUIT', 'EXIT'):
            push_reply('BYE')
            return
        if cmd == 'STATUS':
            parts = ['OK:']
            parts.append('serial=' + ('open' if link.is_open() else 'closed'))
            if camera and camera.available:
                st = camera.state
                port = st['camera_num'] if st['camera_num'] is not None else 'default'
                parts.append(f'camera=on port={port} detected={st["detected"]} '
                             f'res={st["video_size"][0]}x{st["video_size"][1]} q={st["video_quality"]}')
            else:
                err = (camera.last_error if camera else 'disabled')
                parts.append(f'camera=off ({err})')
            if streamer is not None:
                s = streamer.stats
                parts.append(f'frames={s["sent"]} dropped={s["dropped"]} photos={s["photos"]}')
            if lidar and lidar.available:
                running = lidar._thread is not None
                parts.append('lidar=' + ('scanning' if running else 'idle'))
                if lidar_streamer is not None:
                    ls = lidar_streamer.stats
                    parts.append(f'scans={ls["sent"]} scans_dropped={ls["dropped"]}')
            else:
                parts.append('lidar=off')
            push_reply(' '.join(parts))
            return
        if cmd == 'PHOTO':
            if not camera or not camera.available:
                push_reply('ERR: camera unavailable — install python3-picamera2 + enable IMX519 overlay')
                return
            push_reply('OK: PHOTO')

            def _capture_and_push():
                jpeg = camera.capture_photo_jpeg()
                if jpeg and streamer is not None:
                    streamer.push_photo(jpeg)
                else:
                    push_reply('ERR: photo capture failed')
                return False

            # Schedule on the main loop so D-Bus calls stay on-thread; the
            # capture itself can take 200–600 ms which briefly stalls the
            # BLE loop, but that's acceptable for a one-shot photo.
            GLib.idle_add(_capture_and_push)
            return

        # --- Runtime video controls.  All optional; the app uses these to
        # let the user pick Low / Medium / High presets without restarting
        # the bridge.  Each replies with a fresh STATUS so the app can
        # confirm the change took.
        if cmd.startswith('VQ:'):
            if not camera or not camera.available:
                push_reply('ERR: camera unavailable')
                return
            try:
                q = camera.set_video_quality(cmd[3:])
                push_reply(f'OK: VQ={q}')
            except Exception as e:
                push_reply(f'ERR: bad VQ ({e})')
            return
        if cmd.startswith('VFPS:'):
            try:
                fps = max(1, min(15, int(cmd[5:])))
                set_video_fps(fps)
                push_reply(f'OK: VFPS={fps}')
            except Exception as e:
                push_reply(f'ERR: bad VFPS ({e})')
            return
        if cmd.startswith('VRES:'):
            if not camera or not camera.available:
                push_reply('ERR: camera unavailable')
                return
            try:
                w, h = cmd[5:].split('X')
                size = camera.set_video_size(int(w), int(h))
                push_reply(f'OK: VRES={size[0]}x{size[1]}')
            except Exception as e:
                push_reply(f'ERR: bad VRES ({e}); expected WxH e.g. VRES:320x240')
            return
        if cmd == 'VOFF':
            pause_video()
            push_reply('OK: VOFF')
            return
        if cmd == 'VON':
            resume_video()
            push_reply('OK: VON')
            return
        if cmd.startswith('CAM:'):
            if not camera:
                push_reply('ERR: camera disabled with --no-camera')
                return
            try:
                n = int(cmd[4:])
            except Exception:
                push_reply('ERR: bad CAM (expected CAM:0 or CAM:1)')
                return
            # Bouncing the camera takes ~1 s and the live timer keeps
            # firing during the swap.  Stop it first so we don't capture
            # against a half-torn-down sensor.
            was_running = video_state['timer_id'] is not None
            if was_running:
                _stop_video()
            try:
                actual = camera.set_camera_num(n)
                push_reply(f'OK: CAM={actual}')
            except Exception as e:
                push_reply(f'ERR: CAM:{n} ({e})')
            finally:
                if was_running and camera.available:
                    _start_video()
            return
        if cmd == 'CAMINFO':
            if not camera:
                push_reply('ERR: camera disabled with --no-camera')
                return
            cams = camera.cameras
            st = camera.state
            cur = st['camera_num'] if st['camera_num'] is not None else 'default'
            if not cams:
                push_reply(f'OK: CAMINFO detected=0 active={cur} '
                           f'err="{camera.last_error or "no cameras detected by libcamera"}"')
                return
            # Compact summary: just model + location + num per camera so
            # the line fits within a couple of BLE notifications.
            summaries = []
            for info in cams:
                model = info.get('Model', '?')
                num = info.get('Num', '?')
                loc = info.get('Location', '?')
                summaries.append(f'#{num}:{model}@loc{loc}')
            push_reply(f'OK: CAMINFO detected={len(cams)} active={cur} ' + ' '.join(summaries))
            return

        if cmd == 'LIDAR:ON' or cmd == 'LIDARON':
            if not lidar:
                push_reply('ERR: lidar unavailable (disabled with --no-lidar)')
                return
            # Re-probe in case the user just plugged the device in.
            lidar.reprobe()
            if not lidar.available:
                push_reply(f'ERR: lidar unavailable ({lidar.reason})')
                return
            lidar.start()
            push_reply('OK: LIDAR=on')
            return
        if cmd == 'LIDAR:OFF' or cmd == 'LIDAROFF':
            if lidar:
                lidar.stop()
            push_reply('OK: LIDAR=off')
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

    # RX (commands in) — write-only, with our callback.
    robot.add_characteristic(srv_id=1, chr_id=1, uuid=NUS_RX_CHAR,
                             value=[], notifying=False,
                             flags=['write', 'write-without-response'],
                             write_callback=rx_write)
    # TX (text replies out).
    robot.add_characteristic(srv_id=1, chr_id=2, uuid=NUS_TX_CHAR,
                             value=[], notifying=False, flags=['notify'])
    # VIDEO (video + photo chunks out).
    robot.add_characteristic(srv_id=1, chr_id=3, uuid=NUS_VIDEO_CHAR,
                             value=[], notifying=False, flags=['notify'])
    # LIDAR (binary scan chunks out).
    robot.add_characteristic(srv_id=1, chr_id=4, uuid=NUS_LIDAR_CHAR,
                             value=[], notifying=False, flags=['notify'])

    # bluezero stores characteristics in registration order.
    tx_char = robot.characteristics[1]
    video_char = robot.characteristics[2]
    lidar_char = robot.characteristics[3]

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

    streamer = VideoStreamer(video_char)

    # Lidar — wire its background thread into the BLE streamer.  The
    # on_scan callback runs on the rplidar thread, but push_scan() only
    # touches its own state and schedules chunks via GLib.timeout_add,
    # which is thread-safe (GLib serialises the timeouts on the main
    # loop).  No locking required.
    lidar_streamer = LidarStreamer(lidar_char)
    if lidar:
        lidar._on_scan = lidar_streamer.push_scan  # noqa: SLF001
        if args.lidar_autostart and lidar.available:
            lidar.start()

    # Periodic video capture, only kicked off after a client connects so we
    # don't burn camera + CPU + battery while nobody's watching.  Driven by
    # the BlueZ-level connect/disconnect signals below (same path the
    # disconnect safety-stop uses) so we don't depend on bluezero exposing
    # its own on_connect/on_disconnect hooks.
    video_state = {
        'timer_id': None,
        'fps': max(1, min(15, args.video_fps)),
        'paused': False,
        'connected': False,
    }

    def _start_video():
        if not camera or not camera.available:
            return
        if video_state['paused']:
            return
        if video_state['timer_id'] is not None:
            return
        fps = video_state['fps']
        interval_ms = max(50, int(1000 / max(1, fps)))
        print(f'[ble] starting video stream at ~{fps} fps', file=sys.stderr)

        def _tick():
            if video_state['timer_id'] is None:
                return False
            jpeg = camera.capture_video_jpeg()
            if jpeg and streamer is not None:
                streamer.push_video_frame(jpeg)
            return True

        video_state['timer_id'] = GLib.timeout_add(interval_ms, _tick)

    def _stop_video():
        tid = video_state['timer_id']
        if tid is not None:
            try:
                GLib.source_remove(tid)
            except Exception:
                pass
            video_state['timer_id'] = None
            print('[ble] stopped video stream', file=sys.stderr)

    def _set_video_fps(fps):
        video_state['fps'] = max(1, min(15, int(fps)))
        # Bounce the timer so the new interval takes effect immediately,
        # but only if we're actively streaming.
        if video_state['timer_id'] is not None:
            _stop_video()
            _start_video()
    set_video_fps = _set_video_fps  # publish to handle_command  # noqa: F811

    def _pause_video():
        video_state['paused'] = True
        _stop_video()
    def _resume_video():
        video_state['paused'] = False
        # Resume only matters if a client is connected; otherwise we'll
        # naturally start on the next connect.
        if video_state['connected']:
            _start_video()
    pause_video  = _pause_video   # noqa: F811
    resume_video = _resume_video  # noqa: F811

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
    # We also use these callbacks to start / stop the video stream so the
    # camera only runs while a phone is actually watching.
    def _on_connect(_path):
        video_state['connected'] = True
        if watchdog: watchdog.set_connected(True)
        _start_video()
    def _on_disconnect(_path):
        video_state['connected'] = False
        if watchdog: watchdog.set_connected(False)
        _stop_video()
        # Spin the lidar down on disconnect so we don't burn the motor +
        # USB power while nobody is watching.  Re-armed on the next
        # LIDAR:ON.  Honour --lidar-autostart by leaving it running.
        if lidar and not args.lidar_autostart:
            lidar.stop()

    if not args.no_disconnect_stop:
        try:
            install_disconnect_safety_stop(link, on_connect=_on_connect,
                                           on_disconnect=_on_disconnect)
        except Exception as e:
            print(f'WARNING: could not install disconnect safety stop: {e}', file=sys.stderr)
            print('         BLE drops mid-drive will not auto-stop the robot.', file=sys.stderr)
            # Disconnect monitor failed: fall back to streaming continuously
            # so the user still gets video, even though we lose the per-
            # client start/stop optimisation.
            if camera and camera.available:
                print('[ble] streaming continuously (no connect-hook)', file=sys.stderr)
                GLib.idle_add(lambda: (_start_video(), False)[1])
    else:
        # User opted out of the disconnect monitor entirely; we still want
        # video so kick it off on the main loop.
        if camera and camera.available:
            GLib.idle_add(lambda: (_start_video(), False)[1])

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
    if camera and camera.available:
        print(f'Video: {args.video_width}x{args.video_height} @ {args.video_fps} fps. '
              f'Photo: {args.photo_width}x{args.photo_height}.')
    else:
        print('Video / photo disabled (no camera).')
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
        _stop_video()
        if camera:
            camera.close()
        if lidar:
            lidar.stop()


if __name__ == '__main__':
    main()
