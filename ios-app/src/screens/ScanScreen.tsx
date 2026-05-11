// Scan + pair screen.
//
// "Pairing" here = scan for any peripheral advertising the Nordic UART
// Service, list the candidates, let the user tap one to connect. The
// chosen device id is persisted to AsyncStorage so the next launch
// auto-jumps into the Control screen without re-scanning. The BLE
// server has no PIN flow; iOS just bonds on first secure characteristic
// access. From the user's POV: tap robot → "Connected" → drive.

import React, { useEffect, useRef, useState } from 'react';
import {
  View, Text, FlatList, TouchableOpacity, ActivityIndicator,
  StyleSheet, Alert, Platform,
} from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import type { Device } from 'react-native-ble-plx';
import { startScan, waitForPoweredOn, connect } from '../lib/ble';

export const LAST_ROBOT_KEY = 'last_robot_id';

type Props = {
  onConnected: (deviceId: string, name: string) => void;
};

export default function ScanScreen({ onConnected }: Props) {
  const [scanning, setScanning] = useState(false);
  const [devices, setDevices] = useState<Device[]>([]);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [error, setError] = useState<string>('');
  const stopRef = useRef<(() => void) | null>(null);

  // Try auto-reconnect to the last robot we paired with. This is the
  // "stayed paired" UX — open the app and you're already controlling.
  useEffect(() => {
    (async () => {
      try {
        const lastId = await AsyncStorage.getItem(LAST_ROBOT_KEY);
        if (!lastId) {
          beginScan();
          return;
        }
        try {
          await waitForPoweredOn();
          setConnecting(lastId);
          // Re-scan briefly so the device is in ble-plx's cache; iOS
          // refuses to connect to a peripheral it hasn't seen advertise.
          await new Promise<void>((resolve) => {
            const stop = startScan((d) => {
              if (d.id === lastId) {
                stop();
                resolve();
              }
            });
            setTimeout(() => { stop(); resolve(); }, 4000);
          });
          await connect(lastId);
          onConnected(lastId, 'Robot');
        } catch (e: any) {
          setConnecting(null);
          beginScan();
        }
      } catch {
        beginScan();
      }
    })();
    return () => { if (stopRef.current) stopRef.current(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const beginScan = async () => {
    setError('');
    setDevices([]);
    try {
      await waitForPoweredOn();
    } catch (e: any) {
      setError(e?.message || 'Bluetooth is off');
      return;
    }
    setScanning(true);
    const stop = startScan((d) => {
      setDevices((prev) => (prev.find((p) => p.id === d.id) ? prev : [...prev, d]));
    });
    stopRef.current = () => { stop(); setScanning(false); };
    // Auto-stop scan after 20s — anything not advertising by then isn't here.
    setTimeout(() => { if (stopRef.current) stopRef.current(); }, 20000);
  };

  const handleConnect = async (d: Device) => {
    if (stopRef.current) stopRef.current();
    setConnecting(d.id);
    try {
      await connect(d.id);
      await AsyncStorage.setItem(LAST_ROBOT_KEY, d.id);
      onConnected(d.id, d.name || d.localName || 'Robot');
    } catch (e: any) {
      setConnecting(null);
      Alert.alert('Pairing failed', e?.message || 'Could not connect to this robot.');
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Ionicons name="bluetooth" size={32} color="#38bdf8" />
        <Text style={styles.title}>Pair with your robot</Text>
        <Text style={styles.sub}>
          Make sure the robot is powered on and broadcasting BLE. Tap one
          below to pair.
        </Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <FlatList
        data={devices}
        keyExtractor={(d) => d.id}
        renderItem={({ item }) => {
          const label = item.name || item.localName || 'Unknown Robot';
          const isConnecting = connecting === item.id;
          return (
            <TouchableOpacity
              style={styles.row}
              onPress={() => handleConnect(item)}
              disabled={!!connecting}
              activeOpacity={0.8}
            >
              <Ionicons name="hardware-chip-outline" size={22} color="#38bdf8" />
              <View style={{ flex: 1, marginLeft: 12 }}>
                <Text style={styles.rowName}>{label}</Text>
                <Text style={styles.rowId}>{item.id}</Text>
              </View>
              {isConnecting ? (
                <ActivityIndicator color="#38bdf8" />
              ) : (
                <Ionicons name="chevron-forward" size={20} color="#475569" />
              )}
            </TouchableOpacity>
          );
        }}
        ListEmptyComponent={
          <View style={styles.empty}>
            {scanning ? (
              <>
                <ActivityIndicator color="#38bdf8" />
                <Text style={styles.emptyText}>Scanning for robots…</Text>
              </>
            ) : (
              <Text style={styles.emptyText}>
                {connecting ? 'Reconnecting to last robot…' : 'No robots found yet.'}
              </Text>
            )}
          </View>
        }
        contentContainerStyle={{ paddingBottom: 24 }}
      />

      <TouchableOpacity
        style={[styles.rescanBtn, scanning && { opacity: 0.6 }]}
        onPress={beginScan}
        disabled={scanning || !!connecting}
        activeOpacity={0.85}
      >
        <Ionicons name="refresh" size={18} color="#0f172a" />
        <Text style={styles.rescanText}>{scanning ? 'Scanning…' : 'Scan again'}</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a', padding: 16, paddingTop: Platform.OS === 'ios' ? 60 : 24 },
  header: { alignItems: 'center', marginBottom: 24 },
  title: { fontSize: 22, fontWeight: '700', color: '#f1f5f9', marginTop: 10 },
  sub: { fontSize: 13, color: '#94a3b8', textAlign: 'center', marginTop: 6, lineHeight: 18 },
  error: { color: '#f87171', textAlign: 'center', marginBottom: 12 },
  row: {
    flexDirection: 'row', alignItems: 'center',
    backgroundColor: '#1e293b', borderRadius: 12, padding: 14, marginBottom: 10,
    borderWidth: 1, borderColor: '#334155',
  },
  rowName: { color: '#f1f5f9', fontSize: 15, fontWeight: '600' },
  rowId: { color: '#64748b', fontSize: 11, marginTop: 2 },
  empty: { alignItems: 'center', paddingVertical: 40, gap: 10 },
  emptyText: { color: '#94a3b8' },
  rescanBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    backgroundColor: '#38bdf8', borderRadius: 12, paddingVertical: 14, marginTop: 8,
  },
  rescanText: { color: '#0f172a', fontWeight: '700', fontSize: 15 },
});
