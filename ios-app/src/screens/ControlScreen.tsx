// Tank-control pad. Five large buttons map 1:1 to the BLE NUS commands
// the Pi-side ble_server.py writes directly to the Arduino over USB serial:
//   UP / DOWN / LEFT / RIGHT / STOP
// (these mirror src/wifi_client.cpp's keyboard mapping.)
//
// UX (matches the Pi console's `bin/robot wifi-server` keyboard feel):
//   - Press-and-hold = continuous step. We send the command on press AND
//     auto-repeat at REPEAT_INTERVAL_MS while the finger stays down. Each
//     command moves the Arduino's servo levels by ±3 (constrain'd to
//     0..180), so a steady hold ramps roughly neutral → full in ~1.5 s.
//   - Releasing a direction button does NOT auto-STOP. The robot HOLDS
//     its current speed, exactly like releasing an arrow key on the
//     wifi-server console. To halt, press the big red STOP in the centre.
//     This trade gives much smoother starts and stops, instead of the
//     robot snapping back to neutral every time a thumb lifts.
//   - STOP button + a fresh STOP on screen unmount + the Sabertooth's
//     own R/C failsafe (signal-loss → motors off) are the safety nets.
//   - The last reply line from the robot (e.g. "OK: UP") is shown at the
//     bottom so the user can see commands are landing.
//   - Forget / Re-pair button up top wipes the saved robot id and pops
//     back to the scan screen.
//
// Why 50 ms?  The wifi-server's keyboard loop drains buffered keys every
// 50 ms (= one Sabertooth R/C pulse cycle at 50 Hz) and sends at most one
// motor step per loop tick.  Matching that cadence on the app side keeps
// the feel identical between the two control paths, and 20 commands/sec
// is well under BLE's connection-interval bandwidth budget.
const REPEAT_INTERVAL_MS = 50;

import React, { useEffect, useRef, useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet, Platform, Alert,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { connect, RobotConnection } from '../lib/ble';
import { LAST_ROBOT_KEY } from './ScanScreen';

type Props = {
  deviceId: string;
  deviceName: string;
  onDisconnected: () => void;
};

// Reconnect strategy: when BLE drops, try a handful of times with a short
// backoff before giving up.  iOS's BLE stack sometimes refuses an immediate
// reconnect to the same peripheral, so we wait briefly before the first try.
const RECONNECT_ATTEMPTS = 6;
const RECONNECT_DELAYS_MS = [800, 1500, 3000, 5000, 8000, 12000];

export default function ControlScreen({ deviceId, deviceName, onDisconnected }: Props) {
  const connRef = useRef<RobotConnection | null>(null);
  const [status, setStatus] = useState<'connecting' | 'connected' | 'reconnecting' | 'disconnected'>('connecting');
  const [lastReply, setLastReply] = useState<string>('');
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const repeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef<number>(0);
  const cancelledRef = useRef<boolean>(false);

  useEffect(() => {
    cancelledRef.current = false;

    // Single attempt to establish (or re-establish) the BLE connection.
    // Wires up reply notifications and the auto-reconnect-on-disconnect
    // handler.  Called from both the initial useEffect run and from
    // scheduleReconnect() below.
    const attemptConnect = async (isReconnect: boolean) => {
      if (cancelledRef.current) return;
      setStatus(isReconnect ? 'reconnecting' : 'connecting');
      try {
        const conn = await connect(deviceId);
        if (cancelledRef.current) {
          await conn.disconnect();
          return;
        }
        connRef.current = conn;
        reconnectAttemptRef.current = 0;     // success — reset backoff
        setStatus('connected');
        conn.onReply((line) => setLastReply(line));
        conn.device.onDisconnected(() => {
          // Pi side fires a safety STOP on disconnect already (ble_server.py
          // listens to BlueZ's PropertiesChanged), so the robot is already
          // halted — our job here is to claw the connection back.
          connRef.current = null;
          scheduleReconnect();
        });
      } catch (e: any) {
        // eslint-disable-next-line no-console
        console.warn('[ble] connect failed', e?.message);
        // Fall through into the reconnect ladder; if we exhaust attempts,
        // scheduleReconnect will surface the failure.
        scheduleReconnect();
      }
    };

    // Schedule the next reconnect attempt using a small backoff so we
    // don't hammer the BLE stack while it's still tearing the old
    // connection down.  Gives up after RECONNECT_ATTEMPTS tries and
    // shows the user the "Back to pairing" button.
    const scheduleReconnect = () => {
      if (cancelledRef.current) return;
      const n = reconnectAttemptRef.current;
      if (n >= RECONNECT_ATTEMPTS) {
        setStatus('disconnected');
        return;
      }
      const delay = RECONNECT_DELAYS_MS[Math.min(n, RECONNECT_DELAYS_MS.length - 1)];
      reconnectAttemptRef.current = n + 1;
      setStatus('reconnecting');
      reconnectTimerRef.current = setTimeout(() => attemptConnect(true), delay);
    };

    attemptConnect(false);

    return () => {
      cancelledRef.current = true;
      if (repeatTimerRef.current) clearInterval(repeatTimerRef.current);
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      // Best-effort stop + cleanup. Fire-and-forget; nothing to await on unmount.
      const conn = connRef.current;
      if (conn) {
        conn.send('STOP').catch(() => {});
        conn.disconnect().catch(() => {});
      }
    };
  }, [deviceId]);

  const send = async (cmd: string) => {
    const conn = connRef.current;
    if (!conn) return;
    try {
      await conn.send(cmd);
    } catch (e: any) {
      // eslint-disable-next-line no-console
      console.warn('[ble] send failed', e?.message);
    }
  };

  const onPressIn = (cmd: string) => {
    setActiveKey(cmd);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    send(cmd);
    // Auto-repeat at 20 Hz (50 ms) while held — matches the wifi-server's
    // keyboard loop tick so the on-screen pad and the Pi console feel
    // identical.  A 1.5 s hold takes the Arduino's level from neutral
    // (90) to full throttle (0 or 180); shorter taps give proportionally
    // smaller steps.
    if (repeatTimerRef.current) clearInterval(repeatTimerRef.current);
    repeatTimerRef.current = setInterval(() => {
      send(cmd);
    }, REPEAT_INTERVAL_MS);
  };

  const onPressOut = () => {
    if (repeatTimerRef.current) {
      clearInterval(repeatTimerRef.current);
      repeatTimerRef.current = null;
    }
    setActiveKey(null);
    // NB: We deliberately do NOT send 'STOP' here.  Releasing a direction
    // button keeps the robot moving at its current speed, exactly like
    // releasing an arrow key on the wifi-server console — that's what
    // gives the pad a smooth/analog feel instead of snapping to neutral.
    // The big red STOP button below is the explicit way to halt; the
    // useEffect cleanup also fires a STOP on unmount as a safety net.
  };

  const handleForget = () => {
    Alert.alert(
      'Forget robot?',
      'You\'ll need to re-pair on next launch.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Forget',
          style: 'destructive',
          onPress: async () => {
            await AsyncStorage.removeItem(LAST_ROBOT_KEY);
            const c = connRef.current;
            if (c) await c.disconnect();
            onDisconnected();
          },
        },
      ],
    );
  };

  const PadButton = ({ cmd, icon, style }: { cmd: string; icon: string; style?: any }) => (
    <TouchableOpacity
      style={[styles.padBtn, style, activeKey === cmd && styles.padBtnActive]}
      onPressIn={() => onPressIn(cmd)}
      onPressOut={onPressOut}
      activeOpacity={0.85}
      disabled={status !== 'connected'}
    >
      <Ionicons name={icon as any} size={42} color={status === 'connected' ? '#f1f5f9' : '#475569'} />
    </TouchableOpacity>
  );

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <View style={{ flex: 1 }}>
          <Text style={styles.title}>{deviceName}</Text>
          <Text style={styles.subtitle}>
            {status === 'connecting' ? 'Connecting…'
              : status === 'reconnecting' ? `Reconnecting… (try ${reconnectAttemptRef.current}/${RECONNECT_ATTEMPTS})`
              : status === 'connected' ? '● Connected'
              : '○ Disconnected'}
          </Text>
        </View>
        <TouchableOpacity onPress={handleForget} style={styles.forgetBtn}>
          <Ionicons name="close-circle" size={28} color="#94a3b8" />
        </TouchableOpacity>
      </View>

      {status === 'disconnected' && (
        <TouchableOpacity
          style={styles.reconnectBtn}
          onPress={() => onDisconnected()}
          activeOpacity={0.85}
        >
          <Ionicons name="refresh" size={18} color="#0f172a" />
          <Text style={styles.reconnectText}>Back to pairing</Text>
        </TouchableOpacity>
      )}

      <View style={styles.padWrap}>
        <View style={styles.padRow}>
          <View style={styles.padSpacer} />
          <PadButton cmd="UP" icon="arrow-up" />
          <View style={styles.padSpacer} />
        </View>
        <View style={styles.padRow}>
          <PadButton cmd="LEFT" icon="arrow-back" />
          <TouchableOpacity
            style={styles.stopBtn}
            onPress={() => { Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(() => {}); send('STOP'); }}
            disabled={status !== 'connected'}
            activeOpacity={0.85}
          >
            <Text style={styles.stopText}>STOP</Text>
          </TouchableOpacity>
          <PadButton cmd="RIGHT" icon="arrow-forward" />
        </View>
        <View style={styles.padRow}>
          <View style={styles.padSpacer} />
          <PadButton cmd="DOWN" icon="arrow-down" />
          <View style={styles.padSpacer} />
        </View>
      </View>

      <View style={styles.replyBox}>
        <Text style={styles.replyLabel}>ROBOT SAYS</Text>
        <Text style={styles.replyText}>{lastReply || '— no reply yet —'}</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a', padding: 16, paddingTop: Platform.OS === 'ios' ? 60 : 24 },
  header: { flexDirection: 'row', alignItems: 'center', marginBottom: 16 },
  title: { color: '#f1f5f9', fontSize: 22, fontWeight: '700' },
  subtitle: { color: '#94a3b8', fontSize: 13, marginTop: 2 },
  forgetBtn: { padding: 6 },
  reconnectBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    backgroundColor: '#38bdf8', borderRadius: 12, paddingVertical: 12, marginBottom: 12,
  },
  reconnectText: { color: '#0f172a', fontWeight: '700', fontSize: 14 },
  padWrap: { flex: 1, justifyContent: 'center', gap: 14 },
  padRow: { flexDirection: 'row', justifyContent: 'center', gap: 14 },
  padBtn: {
    width: 100, height: 100, borderRadius: 18,
    backgroundColor: '#1e293b', borderWidth: 1, borderColor: '#334155',
    alignItems: 'center', justifyContent: 'center',
  },
  padBtnActive: { backgroundColor: '#0369a1', borderColor: '#38bdf8' },
  padSpacer: { width: 100 },
  stopBtn: {
    width: 100, height: 100, borderRadius: 50,
    backgroundColor: '#dc2626', alignItems: 'center', justifyContent: 'center',
    borderWidth: 2, borderColor: '#fca5a5',
  },
  stopText: { color: '#fff', fontWeight: '900', fontSize: 18, letterSpacing: 1 },
  replyBox: { borderTopWidth: 1, borderTopColor: '#1e293b', paddingTop: 12, marginTop: 12 },
  replyLabel: { color: '#475569', fontSize: 10, letterSpacing: 1.5, marginBottom: 4 },
  replyText: { color: '#cbd5e1', fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 13 },
});
