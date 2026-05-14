// Thin wrapper around react-native-ble-plx for the Nordic UART Service +
// the robot's video stream extension.
//
// The robot's BLE server (scripts/ble_server.py) advertises NUS:
//   service     6E400001-B5A3-F393-E0A9-E50E24DCCA9E
//   write char  6E400002-B5A3-F393-E0A9-E50E24DCCA9E   phone → robot (commands)
//   notify char 6E400003-B5A3-F393-E0A9-E50E24DCCA9E   robot → phone (replies)
//   notify char 6E400004-B5A3-F393-E0A9-E50E24DCCA9E   robot → phone (video frames)
//
// Each write to the RX char is one command — UP, DOWN, LEFT, RIGHT, STOP
// (or the aliases the server accepts: FORWARD, BACK, W, A, S, D, X, STATUS),
// plus the new PHOTO command which asks the Pi to send back a single
// high-res JPEG over the video characteristic.
//
// Video and photo frames share one notify characteristic; see
// ./videoFrames.ts for the chunk-reassembly wire format. Live video is
// flagged with the photo bit clear; the response to PHOTO has it set.
// The Pi side (scripts/ble_server.py) emits chunks via picamera2 (Arducam
// IMX519) at ~4 fps once a phone connects.

import { BleManager, BleError, BleErrorCode, Device, State, Subscription } from 'react-native-ble-plx';
import { Buffer } from 'buffer';
import { CompleteFrame, FrameReassembler } from './videoFrames';
import { LidarScan, LidarReassembler } from './lidarFrames';
import { QualitySettings } from './videoQuality';

// Map a low-level BleError / state into a sentence + an actionable hint
// the user can do something with.  Falls back to the raw message for
// anything we haven't classified yet.
export interface BleProblem {
  title: string;
  detail: string;
  // Suggested next-step button label, if any.
  action?: 'open-settings' | 'retry' | 'rescan';
}

export function describeBleError(e: any): BleProblem {
  const msg: string = (e?.message || String(e || '')).trim();
  const code: number | undefined = e?.errorCode;
  // ble-plx surfaces these consistently across iOS/Android.
  if (code === BleErrorCode.BluetoothPoweredOff) {
    return {
      title: 'Bluetooth is off',
      detail: 'Turn Bluetooth on in iOS Settings, then come back.',
      action: 'open-settings',
    };
  }
  if (code === BleErrorCode.BluetoothUnauthorized) {
    return {
      title: 'Bluetooth permission denied',
      detail: 'Allow Bluetooth for Robot Control in iOS Settings → Robot Control.',
      action: 'open-settings',
    };
  }
  if (code === BleErrorCode.BluetoothUnsupported) {
    return { title: 'No Bluetooth available', detail: 'This device does not support BLE.' };
  }
  if (code === BleErrorCode.DeviceNotFound || code === BleErrorCode.DeviceNotConnected) {
    return {
      title: 'Robot not in range',
      detail: "We can't see the robot. Make sure it's powered on and within ~10 m, then retry.",
      action: 'retry',
    };
  }
  if (code === BleErrorCode.DeviceDisconnected) {
    return {
      title: 'Robot disconnected',
      detail: 'The BLE link dropped. Trying to reconnect…',
      action: 'retry',
    };
  }
  if (code === BleErrorCode.DeviceAlreadyConnected) {
    return { title: 'Already connected', detail: 'iOS thinks the robot is already connected. Retry should clear this.', action: 'retry' };
  }
  if (code === BleErrorCode.OperationTimedOut) {
    return {
      title: 'Connection timed out',
      detail: 'The robot did not respond in time. Make sure it is powered on and try again.',
      action: 'retry',
    };
  }
  return { title: 'Bluetooth error', detail: msg || 'Unknown failure.', action: 'retry' };
}

export const NUS_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
export const NUS_RX_CHAR = '6e400002-b5a3-f393-e0a9-e50e24dcca9e';
export const NUS_TX_CHAR = '6e400003-b5a3-f393-e0a9-e50e24dcca9e';
export const NUS_VIDEO_CHAR = '6e400004-b5a3-f393-e0a9-e50e24dcca9e';
export const NUS_LIDAR_CHAR = '6e400005-b5a3-f393-e0a9-e50e24dcca9e';

export type RobotCommand = 'UP' | 'DOWN' | 'LEFT' | 'RIGHT' | 'STOP' | 'STATUS' | 'PHOTO';

let _manager: BleManager | null = null;
export function getManager(): BleManager {
  if (!_manager) _manager = new BleManager();
  return _manager;
}

export async function waitForPoweredOn(timeoutMs = 8000): Promise<void> {
  const m = getManager();
  const initial = await m.state();
  if (initial === State.PoweredOn) return;
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => {
      sub.remove();
      reject(new Error('Bluetooth did not power on in time. Check iOS Bluetooth setting.'));
    }, timeoutMs);
    const sub: Subscription = m.onStateChange((s) => {
      if (s === State.PoweredOn) {
        clearTimeout(t);
        sub.remove();
        resolve();
      }
    }, true);
  });
}

export type ScanCb = (d: Device) => void;

// Scan for any peripheral advertising the Nordic UART Service. The
// callback is called once per device (we dedupe by id).
export function startScan(onFound: ScanCb): () => void {
  const m = getManager();
  const seen = new Set<string>();
  m.startDeviceScan([NUS_SERVICE], { allowDuplicates: false }, (err, device) => {
    if (err) {
      // eslint-disable-next-line no-console
      console.warn('[ble] scan error', err.message);
      return;
    }
    if (device && !seen.has(device.id)) {
      seen.add(device.id);
      onFound(device);
    }
  });
  return () => {
    try { m.stopDeviceScan(); } catch {}
  };
}

export interface RobotConnection {
  device: Device;
  /** Whether the live video characteristic was found and we're subscribed
   *  to it.  When false, the Pi is on an older firmware that only speaks
   *  commands — control still works, but the video panel will sit empty. */
  hasVideo: boolean;
  /** Whether the lidar characteristic exists on the connected robot. */
  hasLidar: boolean;
  disconnect: () => Promise<void>;
  send: (cmd: string) => Promise<void>;
  onReply: (cb: (line: string) => void) => () => void;
  onFrame: (cb: (frame: CompleteFrame) => void) => () => void;
  onLidarScan: (cb: (scan: LidarScan) => void) => () => void;
  requestPhoto: () => Promise<void>;
  /** Send VRES/VQ/VFPS so the Pi switches the live stream to a new
   *  quality preset.  Cheap to call repeatedly; the Pi answers with
   *  OK: VRES=… / VQ=… / VFPS=… on the TX channel. */
  applyQuality: (q: QualitySettings) => Promise<void>;
  /** Pause/resume the live stream from the Pi.  Useful when the app
   *  goes into the background — no point burning the camera. */
  setStreaming: (on: boolean) => Promise<void>;
  /** Start/stop the RPLidar's continuous scan.  The Pi replies with
   *  OK: LIDAR=on / OK: LIDAR=off, or ERR: lidar unavailable (…). */
  setLidar: (on: boolean) => Promise<void>;
}

export async function connect(deviceId: string): Promise<RobotConnection> {
  const m = getManager();
  // ble-plx returns the existing device handle (advertising data attached)
  // if we've seen it during a scan; otherwise we need to connect fresh.
  try { await m.stopDeviceScan(); } catch {}
  const device = await m.connectToDevice(deviceId, { autoConnect: false, timeout: 12000 });
  // Bigger MTU = bigger BLE notifications = fewer chunks per JPEG frame.
  // Android exposes requestMTU; iOS auto-negotiates and ignores requests
  // (the call rejects silently). Either way, do best-effort.
  try { await device.requestMTU(517); } catch {}
  await device.discoverAllServicesAndCharacteristics();

  const subs: Subscription[] = [];
  const replyCbs: Array<(line: string) => void> = [];
  const frameCbs: Array<(frame: CompleteFrame) => void> = [];
  const lidarCbs: Array<(scan: LidarScan) => void> = [];
  const reassembler = new FrameReassembler();
  const lidarReassembler = new LidarReassembler();

  // Subscribe to TX notifications so callers get the robot's text replies.
  subs.push(device.monitorCharacteristicForService(
    NUS_SERVICE,
    NUS_TX_CHAR,
    (err, characteristic) => {
      if (err) {
        // eslint-disable-next-line no-console
        console.warn('[ble] tx monitor error', err.message);
        return;
      }
      const b64 = characteristic?.value;
      if (!b64) return;
      const text = Buffer.from(b64, 'base64').toString('utf8').replace(/\r?\n/g, '\n');
      for (const line of text.split('\n')) {
        if (line) for (const cb of replyCbs) cb(line);
      }
    },
  ));

  // Subscribe to the video characteristic. If the Pi server hasn't been
  // updated to expose it yet, monitor will surface a "characteristic not
  // found" error on the first callback — flip hasVideo off so the UI can
  // tell the user. Commands still work either way.
  let hasVideo = true;
  let hasLidar = false;
  const services = await device.services();
  const nus = services.find(s => s.uuid.toLowerCase() === NUS_SERVICE);
  if (nus) {
    const chars = await nus.characteristics();
    hasVideo = chars.some(c => c.uuid.toLowerCase() === NUS_VIDEO_CHAR);
    hasLidar = chars.some(c => c.uuid.toLowerCase() === NUS_LIDAR_CHAR);
  }
  if (hasVideo) {
    try {
      subs.push(device.monitorCharacteristicForService(
        NUS_SERVICE,
        NUS_VIDEO_CHAR,
        (err, characteristic) => {
          if (err) {
            // Don't spam — but do surface the first failure so debugging
            // a flaky link isn't a black box.
            // eslint-disable-next-line no-console
            console.warn('[ble] video monitor error', err.message);
            return;
          }
          const b64 = characteristic?.value;
          if (!b64) return;
          const bytes = new Uint8Array(Buffer.from(b64, 'base64'));
          const frame = reassembler.ingest(bytes);
          if (frame) {
            for (const cb of frameCbs) cb(frame);
          }
        },
      ));
    } catch (e: any) {
      hasVideo = false;
      // eslint-disable-next-line no-console
      console.warn('[ble] video subscribe failed', e?.message);
    }
  }

  // Lidar subscription — same pattern as video.
  if (hasLidar) {
    try {
      subs.push(device.monitorCharacteristicForService(
        NUS_SERVICE,
        NUS_LIDAR_CHAR,
        (err, characteristic) => {
          if (err) {
            // eslint-disable-next-line no-console
            console.warn('[ble] lidar monitor error', err.message);
            return;
          }
          const b64 = characteristic?.value;
          if (!b64) return;
          const bytes = new Uint8Array(Buffer.from(b64, 'base64'));
          const scan = lidarReassembler.ingest(bytes);
          if (scan) {
            for (const cb of lidarCbs) cb(scan);
          }
        },
      ));
    } catch (e: any) {
      hasLidar = false;
      // eslint-disable-next-line no-console
      console.warn('[ble] lidar subscribe failed', e?.message);
    }
  }

  const conn: RobotConnection = {
    device,
    hasVideo,
    hasLidar,
    disconnect: async () => {
      for (const s of subs) try { s.remove(); } catch {}
      reassembler.reset();
      lidarReassembler.reset();
      try { await device.cancelConnection(); } catch {}
    },
    send: async (cmd: string) => {
      const payload = Buffer.from(cmd.endsWith('\n') ? cmd : cmd + '\n', 'utf8').toString('base64');
      // Write WITHOUT response — fastest path; the server doesn't ack the
      // write itself, it acks via the TX notify with "OK: <cmd>".
      await device.writeCharacteristicWithoutResponseForService(
        NUS_SERVICE,
        NUS_RX_CHAR,
        payload,
      );
    },
    onReply: (cb) => {
      replyCbs.push(cb);
      return () => {
        const i = replyCbs.indexOf(cb);
        if (i >= 0) replyCbs.splice(i, 1);
      };
    },
    onFrame: (cb) => {
      frameCbs.push(cb);
      return () => {
        const i = frameCbs.indexOf(cb);
        if (i >= 0) frameCbs.splice(i, 1);
      };
    },
    onLidarScan: (cb) => {
      lidarCbs.push(cb);
      return () => {
        const i = lidarCbs.indexOf(cb);
        if (i >= 0) lidarCbs.splice(i, 1);
      };
    },
    requestPhoto: async () => {
      await conn.send('PHOTO');
    },
    setLidar: async (on) => {
      await conn.send(on ? 'LIDAR:ON' : 'LIDAR:OFF');
    },
    applyQuality: async (q) => {
      // Resolution change is the heaviest (camera reconfigure on the Pi
      // takes ~1 s); fps + quality are essentially free.  Send res first
      // so the gap is over before the new fps/quality kick in.
      await conn.send(`VRES:${q.width}x${q.height}`);
      await conn.send(`VQ:${q.quality}`);
      await conn.send(`VFPS:${q.fps}`);
    },
    setStreaming: async (on) => {
      await conn.send(on ? 'VON' : 'VOFF');
    },
  };
  return conn;
}
