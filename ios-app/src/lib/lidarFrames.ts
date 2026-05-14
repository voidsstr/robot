// Reassembly of lidar scans streamed over the lidar BLE notify
// characteristic (NUS_LIDAR_CHAR). Each scan is split across many small
// BLE notifications because notify payloads top out around 180 bytes.
//
// Wire format — each notification carries one chunk (mirrors
// scripts/ble_server.py LidarStreamer):
//
//   byte 0   SCAN_ID     rolling counter, identifies the 360° scan
//   byte 1   CHUNK_IDX   0-based index within the scan
//   byte 2   TOTAL       total chunks for the scan
//   byte 3   FLAGS       reserved (must be 0)
//   bytes 4… POINTS      N × 4-byte points
//       uint16 little-endian: angle in centi-degrees (0..35999)
//       uint16 little-endian: distance in millimeters (0..65535)
//
// A complete scan = SCAN_ID's CHUNK_IDX 0..TOTAL-1. We tolerate
// out-of-order chunks and drop scans where any chunk is missing — lidar
// is best-effort like video and the next scan arrives in ~100 ms.

export interface LidarPoint {
  /** Angle in degrees, 0 = forward (the lidar's own zero), increasing
   *  clockwise. RPLidar reports angles this way; we don't transform them. */
  angleDeg: number;
  /** Distance in meters (NaN if the original reading was 0 = no return). */
  distanceM: number;
}

export interface LidarScan {
  scanId: number;
  /** Wall-clock arrival time (ms since epoch) — set by the reassembler
   *  when the last chunk lands.  Useful for staleness detection. */
  receivedAt: number;
  points: LidarPoint[];
}

const HEADER_LEN = 4;
const POINT_LEN = 4;

interface Partial {
  scanId: number;
  total: number;
  chunks: Array<Uint8Array | undefined>;
  received: number;
  createdAt: number;
}

export class LidarReassembler {
  private partials = new Map<number, Partial>();
  // A new scan completes roughly every 100-200 ms.  Anything still
  // partial after 1 s has lost a chunk and won't be completed.
  private readonly STALE_MS = 1000;

  ingest(packet: Uint8Array): LidarScan | null {
    if (packet.length < HEADER_LEN) return null;
    const scanId = packet[0];
    const chunkIdx = packet[1];
    const total = packet[2];
    // FLAGS (packet[3]) is reserved.
    if (total === 0 || chunkIdx >= total) return null;

    let p = this.partials.get(scanId);
    if (!p || p.total !== total) {
      p = { scanId, total, chunks: new Array(total), received: 0, createdAt: Date.now() };
      this.partials.set(scanId, p);
      this.evictStale();
    }
    if (p.chunks[chunkIdx] === undefined) {
      p.chunks[chunkIdx] = packet.slice(HEADER_LEN);
      p.received++;
    }
    if (p.received < p.total) return null;

    // All chunks present → decode points.  Concatenating into one
    // Uint8Array lets us read uint16s with a single DataView.
    let totalLen = 0;
    for (const c of p.chunks) totalLen += c ? c.length : 0;
    const buf = new Uint8Array(totalLen);
    let off = 0;
    for (const c of p.chunks) {
      if (!c) continue;
      buf.set(c, off);
      off += c.length;
    }
    const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);

    const points: LidarPoint[] = [];
    for (let i = 0; i + POINT_LEN <= buf.length; i += POINT_LEN) {
      const cdeg = dv.getUint16(i, true);          // little-endian
      const dmm  = dv.getUint16(i + 2, true);
      points.push({
        angleDeg: cdeg / 100.0,
        distanceM: dmm === 0 ? NaN : dmm / 1000.0,
      });
    }

    this.partials.delete(scanId);
    return { scanId, receivedAt: Date.now(), points };
  }

  reset() { this.partials.clear(); }

  private evictStale() {
    const now = Date.now();
    for (const [id, p] of this.partials) {
      if (now - p.createdAt > this.STALE_MS) this.partials.delete(id);
    }
  }
}
