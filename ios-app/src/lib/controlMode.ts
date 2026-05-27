// Which on-screen control to show on ControlScreen.
//
//   dpad     — five tank buttons (UP/DOWN/LEFT/RIGHT/STOP), press-and-hold
//              auto-repeat. Original mode, best for precise nudging.
//   joystick — single draggable thumbstick. Same wire protocol (each tick
//              sends UP/DOWN/LEFT/RIGHT based on stick position) but feels
//              continuous and supports diagonals naturally.
//
// The choice is just a UI layer — both modes emit the same BLE commands at
// the same 50 ms cadence, so the Pi and Arduino don't need to know which
// one is in use.

import AsyncStorage from '@react-native-async-storage/async-storage';

const PREF_KEY = '@robot-control/control-mode';

export type ControlMode = 'dpad' | 'joystick';

export const DEFAULT_MODE: ControlMode = 'dpad';

export async function getControlMode(): Promise<ControlMode> {
  const raw = await AsyncStorage.getItem(PREF_KEY);
  if (raw === 'dpad' || raw === 'joystick') return raw;
  return DEFAULT_MODE;
}

export async function setControlMode(m: ControlMode): Promise<void> {
  await AsyncStorage.setItem(PREF_KEY, m);
}
