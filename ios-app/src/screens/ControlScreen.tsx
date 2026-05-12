// Tank-control pad. Five large buttons map 1:1 to the BLE NUS commands
// the Pi-side ble_server.py writes directly to the Arduino over USB serial:
//   UP / DOWN / LEFT / RIGHT / STOP
// (these mirror src/wifi_client.cpp's keyboard mapping.)
//
// UX:
//   - Press-and-hold = continuous step. We send the command on press AND
//     fire a STOP on release so the robot doesn't keep coasting if the
//     thumb slips. The Arduino's accelerate/decelerate logic in
//     src/Arduino/robot/robot.ino moves servo levels by ±3 per command,
//     so this gives a feel close to "longer press = faster".
//   - Big red STOP in the center always parks both treads to neutral.
//   - The last reply line from the robot (e.g. "OK: UP") is shown at the
//     bottom so the user can see commands are landing.
//   - Forget / Re-pair button up top wipes the saved robot id and pops
//     back to the scan screen.

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

export default function ControlScreen({ deviceId, deviceName, onDisconnected }: Props) {
  const connRef = useRef<RobotConnection | null>(null);
  const [status, setStatus] = useState<'connecting' | 'connected' | 'disconnected'>('connecting');
  const [lastReply, setLastReply] = useState<string>('');
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const repeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const conn = await connect(deviceId);
        if (cancelled) {
          await conn.disconnect();
          return;
        }
        connRef.current = conn;
        setStatus('connected');
        conn.onReply((line) => setLastReply(line));
        conn.device.onDisconnected(() => {
          setStatus('disconnected');
        });
      } catch (e: any) {
        Alert.alert('Connection lost', e?.message || 'Robot is unreachable.');
        setStatus('disconnected');
      }
    })();
    return () => {
      cancelled = true;
      if (repeatTimerRef.current) clearInterval(repeatTimerRef.current);
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
    // Auto-repeat at 5 Hz while held — mirrors the wifi_client cadence
    // and keeps the tread acceleration smooth instead of one-tap-per-step.
    if (repeatTimerRef.current) clearInterval(repeatTimerRef.current);
    repeatTimerRef.current = setInterval(() => {
      send(cmd);
    }, 200);
  };

  const onPressOut = () => {
    if (repeatTimerRef.current) {
      clearInterval(repeatTimerRef.current);
      repeatTimerRef.current = null;
    }
    setActiveKey(null);
    // Safety stop on release so the robot doesn't drift away.
    send('STOP');
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
