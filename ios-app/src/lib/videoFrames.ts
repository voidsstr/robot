// Reassembly of JPEG frames streamed over a single BLE notify characteristic.
//
// BLE notifications are tiny (typically 20–512 bytes after MTU negotiation),
// so the Pi must split each JPEG frame into chunks. This module owns the
// wire format and the reassembly state machine; ble.ts wires it up to the
// characteristic and the UI consumes whole frames via callbacks.
//
// Wire format — each BLE notification carries one chunk:
//
//   byte 0   FRAME_ID    rolling counter (0–255), identifies the frame
//   byte 1   CHUNK_IDX   0-based index of this chunk within the frame
//   byte 2   TOTAL       total number of chunks for this frame (1–255)
//   byte 3   FLAGS       bit 0 = 1 if this is a high-res "photo" response
//                        bits 1–7 reserved (must be 0)
//   bytes 4… JPEG_DATA   chunk of the JPEG file, in order
//
// A frame is "complete" when we have received CHUNK_IDX 0…TOTAL-1 for the
// same FRAME_ID. Out-of-order chunks are tolerated; a missed chunk drops
// the whole frame on the floor and we move on to the next FRAME_ID — there
// is no retransmit. Live video is lossy by design; photos retry at the
// command layer.

export interface CompleteFrame {
  jpegBase64: string;  // ready for <Image source={{ uri: `data:image/jpeg;base64,${...}` }} />
  isPhoto: boolean;
  frameId: number;
}

const HEADER_LEN = 4;
const FLAG_PHOTO = 0x01;

interface PartialFrame {
  frameId: number;
  total: number;
  isPhoto: boolean;
  chunks: Array<Uint8Array | undefined>;
  received: number;
  createdAt: number;
}

export class FrameReassembler {
  // Keep at most two in-flight frames (current + next, in case chunks
  // arrive interleaved across a frame boundary). Anything older is
  // dropped — BLE is best-effort, no point hoarding stale partials.
  private partials = new Map<number, PartialFrame>();
  private lastEmittedFrameId: number | null = null;

  // Drop partials older than this many ms. The Pi targets ~5 fps, so
  // anything still incomplete after a second has lost a chunk and is junk.
  private readonly STALE_MS = 1000;

  /** Feed one BLE notification payload. Returns a complete frame if this
   *  chunk finished one, otherwise null. */
  ingest(packet: Uint8Array): CompleteFrame | null {
    if (packet.length < HEADER_LEN + 1) return null;

    const frameId = packet[0];
    const chunkIdx = packet[1];
    const total = packet[2];
    const flags = packet[3];
    const isPhoto = (flags & FLAG_PHOTO) !== 0;

    if (total === 0 || chunkIdx >= total) return null;

    let partial = this.partials.get(frameId);
    if (!partial) {
      partial = {
        frameId,
        total,
        isPhoto,
        chunks: new Array(total),
        received: 0,
        createdAt: Date.now(),
      };
      this.partials.set(frameId, partial);
      this.evictStale();
    } else if (partial.total !== total) {
      // Conflicting total for the same frame id — the sender's counter
      // rolled over onto our partial. Restart this slot.
      partial = {
        frameId, total, isPhoto,
        chunks: new Array(total),
        received: 0,
        createdAt: Date.now(),
      };
      this.partials.set(frameId, partial);
    }

    if (partial.chunks[chunkIdx] === undefined) {
      partial.chunks[chunkIdx] = packet.slice(HEADER_LEN);
      partial.received++;
    }

    if (partial.received < partial.total) return null;

    // All chunks present — concatenate and emit.
    let totalLen = 0;
    for (const c of partial.chunks) totalLen += c ? c.length : 0;
    const jpeg = new Uint8Array(totalLen);
    let off = 0;
    for (const c of partial.chunks) {
      if (!c) continue;
      jpeg.set(c, off);
      off += c.length;
    }

    this.partials.delete(frameId);
    this.lastEmittedFrameId = frameId;

    return {
      jpegBase64: bytesToBase64(jpeg),
      isPhoto: partial.isPhoto,
      frameId,
    };
  }

  reset() {
    this.partials.clear();
    this.lastEmittedFrameId = null;
  }

  private evictStale() {
    const now = Date.now();
    for (const [id, p] of this.partials) {
      if (now - p.createdAt > this.STALE_MS) this.partials.delete(id);
    }
  }
}

// btoa() exists in RN but only handles binary strings. Build base64 from
// a Uint8Array directly so we don't choke on non-latin1 bytes.
const B64_ALPHA = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
export function bytesToBase64(bytes: Uint8Array): string {
  let out = '';
  let i = 0;
  for (; i + 2 < bytes.length; i += 3) {
    const n = (bytes[i] << 16) | (bytes[i + 1] << 8) | bytes[i + 2];
    out += B64_ALPHA[(n >> 18) & 63] + B64_ALPHA[(n >> 12) & 63]
         + B64_ALPHA[(n >> 6) & 63]  + B64_ALPHA[n & 63];
  }
  if (i < bytes.length) {
    const r = bytes.length - i;
    const n = (bytes[i] << 16) | ((r > 1 ? bytes[i + 1] : 0) << 8);
    out += B64_ALPHA[(n >> 18) & 63] + B64_ALPHA[(n >> 12) & 63]
         + (r > 1 ? B64_ALPHA[(n >> 6) & 63] : '=') + '=';
  }
  return out;
}
