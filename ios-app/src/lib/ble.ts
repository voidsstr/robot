// Thin wrapper around react-native-ble-plx for the Nordic UART Service.
//
// The robot's BLE server (scripts/ble_server.py) advertises NUS:
//   service     6E400001-B5A3-F393-E0A9-E50E24DCCA9E
//   write char  6E400002-B5A3-F393-E0A9-E50E24DCCA9E   phone → robot
//   notify char 6E400003-B5A3-F393-E0A9-E50E24DCCA9E   robot → phone (replies)
//
// Each write to the RX char is one command — UP, DOWN, LEFT, RIGHT, STOP
// (or the aliases the server accepts: FORWARD, BACK, W, A, S, D, X, STATUS).
// Trailing newline is optional; the server strips it.

import { BleManager, Device, State, Subscription } from 'react-native-ble-plx';
import { Buffer } from 'buffer';

export const NUS_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
export const NUS_RX_CHAR = '6e400002-b5a3-f393-e0a9-e50e24dcca9e';
export const NUS_TX_CHAR = '6e400003-b5a3-f393-e0a9-e50e24dcca9e';

export type RobotCommand = 'UP' | 'DOWN' | 'LEFT' | 'RIGHT' | 'STOP' | 'STATUS';

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
  disconnect: () => Promise<void>;
  send: (cmd: string) => Promise<void>;
  onReply: (cb: (line: string) => void) => () => void;
}

export async function connect(deviceId: string): Promise<RobotConnection> {
  const m = getManager();
  // ble-plx returns the existing device handle (advertising data attached)
  // if we've seen it during a scan; otherwise we need to connect fresh.
  try { await m.stopDeviceScan(); } catch {}
  const device = await m.connectToDevice(deviceId, { autoConnect: false, timeout: 12000 });
  await device.discoverAllServicesAndCharacteristics();

  const subs: Subscription[] = [];
  const replyCbs: Array<(line: string) => void> = [];

  // Subscribe to TX notifications so callers get the robot's replies.
  const txSub = device.monitorCharacteristicForService(
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
  );
  subs.push(txSub);

  return {
    device,
    disconnect: async () => {
      for (const s of subs) try { s.remove(); } catch {}
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
  };
}
