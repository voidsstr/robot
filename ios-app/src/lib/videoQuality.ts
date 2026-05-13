// Video quality presets for the BLE live stream.
//
// BLE realistic bandwidth on iOS is roughly 30–80 kB/s after MTU
// negotiation and connection-interval overhead.  Higher targets cause
// frames to back up and the Pi-side streamer drops them (latest-wins),
// so the picker is really "how often do we want to drop frames vs how
// much detail per frame."
//
// The Pi accepts these as runtime commands (see scripts/ble_server.py):
//   VRES:<w>x<h>   reconfigure picamera2 — brief gap while it restarts
//   VQ:<n>         JPEG quality 1-95
//   VFPS:<n>       target frame rate 1-15

import AsyncStorage from '@react-native-async-storage/async-storage';

const PREF_KEY = '@robot-control/video-quality';

export type QualityPreset = 'low' | 'medium' | 'high';

export interface QualitySettings {
  width: number;
  height: number;
  quality: number;   // JPEG quality, 1-95
  fps: number;       // target frames per second, 1-15
  label: string;
  hint: string;
}

// Tuned for BLE: at 4 fps × 320×240 × Q60 a frame is ~8 kB → ~32 kB/s,
// safely inside what iOS will sustain over a single notify characteristic.
// High pushes resolution up but keeps fps modest so the streamer doesn't
// thrash; Low is for fragile environments (interference, long range).
export const PRESETS: Record<QualityPreset, QualitySettings> = {
  low: {
    width: 240, height: 180, quality: 40, fps: 3,
    label: 'Low',
    hint: 'Smallest, most reliable. Good for weak signal or long range.',
  },
  medium: {
    width: 320, height: 240, quality: 60, fps: 4,
    label: 'Medium',
    hint: 'Balanced detail and smoothness. Default.',
  },
  high: {
    width: 480, height: 360, quality: 70, fps: 5,
    label: 'High',
    hint: 'Sharper image, more bandwidth. Needs a strong link.',
  },
};

export const DEFAULT_PRESET: QualityPreset = 'medium';

export async function getQualityPreset(): Promise<QualityPreset> {
  const raw = await AsyncStorage.getItem(PREF_KEY);
  if (raw === 'low' || raw === 'medium' || raw === 'high') return raw;
  return DEFAULT_PRESET;
}

export async function setQualityPreset(p: QualityPreset): Promise<void> {
  await AsyncStorage.setItem(PREF_KEY, p);
}

export function settingsFor(p: QualityPreset): QualitySettings {
  return PRESETS[p];
}
