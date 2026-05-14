// Lidar-on-map overlay.
//
// Idea: the robot is "approximately where the phone is" (the operator is
// nearby while driving), so we anchor the lidar's local frame to the
// phone's GPS fix. Each lidar point is converted from (angle, distance)
// in the robot frame into a (lat, lng) offset and drawn as a Polyline
// vertex — that way the map's normal pan/zoom does the scaling for us:
// at any zoom level the dots stay at their real-world positions
// relative to the robot.
//
// Wire format note: RPLidar's angle 0° points "forward" in its own
// frame. Without a magnetometer fix we treat 0° as map-north. The map
// has a compass button so the user can rotate the view to match the
// robot's facing.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, Modal, TouchableOpacity, ActivityIndicator,
  Platform, Linking, Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import MapView, {
  Marker, Polyline, UrlTile, Region, PROVIDER_DEFAULT,
} from 'react-native-maps';
import * as Location from 'expo-location';
import { LidarScan, LidarPoint } from '../lib/lidarFrames';
import { RobotConnection } from '../lib/ble';

interface Props {
  visible: boolean;
  conn: RobotConnection | null;
  /** True iff status === 'connected' in the parent — disables the map's
   *  start/stop button when the link is down. */
  connected: boolean;
  onClose: () => void;
}

// Minimum quality for a lidar return to be drawn (distance in meters).
// Below this it's almost certainly the robot's own chassis or specular
// noise; above 12 m an A1 starts losing accuracy.
const MIN_RANGE_M = 0.10;
const MAX_RANGE_M = 12.0;

// 1° of latitude is ~111,320 m everywhere on Earth.
const M_PER_DEG_LAT = 111320;

export default function MapModal({ visible, conn, connected, onClose }: Props) {
  const mapRef = useRef<MapView | null>(null);
  const [origin, setOrigin] = useState<{ latitude: number; longitude: number } | null>(null);
  const [permissionDenied, setPermissionDenied] = useState(false);
  const [scan, setScan] = useState<LidarScan | null>(null);
  // Discriminated union of the lidar's current state from the Pi's POV.
  // 'starting' = LIDAR:ON sent, awaiting first scan or an error.
  // 'scanning' = at least one scan has landed.
  // 'error'    = the Pi replied with ERR (device missing, perms, etc.).
  // 'idle'     = no LIDAR:ON in flight (closed map or before-first-open).
  type LidarStatus =
    | { kind: 'idle' }
    | { kind: 'starting' }
    | { kind: 'scanning' }
    | { kind: 'error'; reason: string };
  const [lidarStatus, setLidarStatus] = useState<LidarStatus>({ kind: 'idle' });
  // Heading override: how many degrees clockwise the robot's "forward" is
  // off from map-north. User can spin this to align the scan with what
  // they see physically. Persisted only for the lifetime of the modal —
  // the next session can re-orient.
  const [headingOffset, setHeadingOffset] = useState(0);

  // ─── Location ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (!visible) return;
    let cancelled = false;
    (async () => {
      const { status } = await Location.requestForegroundPermissionsAsync();
      if (status !== 'granted') {
        if (!cancelled) setPermissionDenied(true);
        return;
      }
      try {
        const pos = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.High,
        });
        if (!cancelled) {
          setOrigin({ latitude: pos.coords.latitude, longitude: pos.coords.longitude });
          setPermissionDenied(false);
        }
      } catch {
        // last-known fallback so we have something on the screen even if
        // the GPS hasn't acquired yet
        try {
          const last = await Location.getLastKnownPositionAsync();
          if (last && !cancelled) {
            setOrigin({ latitude: last.coords.latitude, longitude: last.coords.longitude });
          }
        } catch {}
      }
    })();
    return () => { cancelled = true; };
  }, [visible]);

  // ─── Lidar lifecycle ───────────────────────────────────────────────────
  // Tracks the latest reply so the Retry button can re-issue LIDAR:ON
  // without re-running the whole effect.
  const sendLidarOn = (c: RobotConnection) => {
    setLidarStatus({ kind: 'starting' });
    c.setLidar(true).catch(() => {});
    c.send('STATUS').catch(() => {});
  };

  useEffect(() => {
    if (!visible || !conn || !conn.hasLidar) return;
    let unsub: (() => void) | null = null;
    let cancelled = false;

    // Parse TX replies into a typed status.  The Pi sends either
    //   "OK: LIDAR=on"
    //   "OK: LIDAR=off"
    //   "ERR: lidar unavailable (<reason>)"
    // The reason string is human-readable and tells the user what to fix
    // (plug it in, install rplidar, fix permissions, etc.).
    const unsubReply = conn.onReply(line => {
      if (cancelled) return;
      if (/^OK: LIDAR=on/i.test(line)) {
        // Don't downgrade scanning → starting if scans are already coming in.
        setLidarStatus(prev => prev.kind === 'scanning' ? prev : { kind: 'starting' });
      } else if (/^OK: LIDAR=off/i.test(line)) {
        setLidarStatus({ kind: 'idle' });
      } else if (/^ERR.*lidar/i.test(line)) {
        // Pull whatever's after the first ':' as the reason.  Fall back
        // to the whole line if the format ever changes.
        const m = line.match(/^ERR[:\s-]*(.+)$/i);
        setLidarStatus({ kind: 'error', reason: m ? m[1].trim() : line });
      }
    });

    unsub = conn.onLidarScan(s => {
      if (cancelled) return;
      setScan(s);
      setLidarStatus({ kind: 'scanning' });
    });

    sendLidarOn(conn);

    return () => {
      cancelled = true;
      if (unsub) unsub();
      unsubReply();
      conn.setLidar(false).catch(() => {});
      setLidarStatus({ kind: 'idle' });
    };
  }, [visible, conn]);

  // ─── Convert lidar points to map coordinates ───────────────────────────
  // We project each point onto a small flat plane tangent to the Earth at
  // the origin — for distances under ~100 m the curvature error is sub-cm.
  const polylinePoints = useMemo(() => {
    if (!origin || !scan) return [];
    const cosLat = Math.cos((origin.latitude * Math.PI) / 180);
    const out: Array<{ latitude: number; longitude: number }> = [];
    // Sort by angle so the polyline traces the obstacle outline cleanly
    // instead of zig-zagging across the scan.
    const ordered = [...scan.points]
      .filter(p => p.distanceM >= MIN_RANGE_M && p.distanceM <= MAX_RANGE_M && !Number.isNaN(p.distanceM))
      .sort((a, b) => a.angleDeg - b.angleDeg);
    for (const p of ordered) {
      const angleRad = ((p.angleDeg + headingOffset) * Math.PI) / 180;
      // Lidar convention: 0° = forward (north in our anchor), clockwise.
      // North = +lat, East = +lng, so:
      const dNorth = p.distanceM * Math.cos(angleRad);
      const dEast  = p.distanceM * Math.sin(angleRad);
      out.push({
        latitude:  origin.latitude  + dNorth / M_PER_DEG_LAT,
        longitude: origin.longitude + dEast  / (M_PER_DEG_LAT * cosLat),
      });
    }
    // Close the loop so the obstacle outline becomes a proper shape.
    if (out.length > 2) out.push(out[0]);
    return out;
  }, [origin, scan, headingOffset]);

  const region: Region | undefined = origin ? {
    latitude: origin.latitude,
    longitude: origin.longitude,
    // ~30 m × 30 m default viewport. cos(lat) corrects for the longitude
    // squish near the poles so the box stays roughly square.
    latitudeDelta:  0.00045,
    longitudeDelta: 0.00045 / Math.max(0.1, Math.cos((origin.latitude * Math.PI) / 180)),
  } : undefined;

  const recenter = () => {
    if (mapRef.current && region) mapRef.current.animateToRegion(region, 350);
  };

  return (
    <Modal animationType="slide" presentationStyle="pageSheet" visible={visible} onRequestClose={onClose}>
      <View style={styles.container}>
        <View style={styles.header}>
          <Text style={styles.title}>Lidar map</Text>
          <TouchableOpacity onPress={onClose} hitSlop={10} style={styles.headerBtn}>
            <Ionicons name="close" size={26} color="#94a3b8" />
          </TouchableOpacity>
        </View>

        {permissionDenied ? (
          <View style={styles.errorBox}>
            <Ionicons name="warning" size={22} color="#fbbf24" />
            <Text style={styles.errorTitle}>Location permission denied</Text>
            <Text style={styles.errorDetail}>
              The lidar map anchors the robot to your phone's GPS. Grant
              location access in iOS Settings → Robot Control.
            </Text>
            <TouchableOpacity
              style={styles.actionBtn}
              onPress={() => Linking.openURL('app-settings:').catch(() => Alert.alert('Open Settings'))}
            >
              <Text style={styles.actionText}>Open iOS Settings</Text>
            </TouchableOpacity>
          </View>
        ) : !conn || !conn.hasLidar ? (
          <View style={styles.errorBox}>
            <Ionicons name="information-circle" size={22} color="#38bdf8" />
            <Text style={styles.errorTitle}>Lidar not available</Text>
            <Text style={styles.errorDetail}>
              {!conn
                ? 'Not connected to the robot.'
                : 'This robot is on older firmware that does not expose the lidar characteristic. Update scripts/ble_server.py on the Pi and restart it. The lidar also needs to be plugged in to /dev/ttyUSB0.'}
            </Text>
          </View>
        ) : !origin ? (
          <View style={styles.loadingBox}>
            <ActivityIndicator color="#38bdf8" />
            <Text style={styles.loadingText}>Waiting for GPS fix…</Text>
          </View>
        ) : (
          <>
            <MapView
              ref={mapRef}
              provider={PROVIDER_DEFAULT}
              style={styles.map}
              initialRegion={region}
              showsUserLocation={false}
              showsCompass
              rotateEnabled
              pitchEnabled={false}
            >
              {/* OpenStreetMap tile overlay — sits on top of the platform
                  default (Apple Maps on iOS). Keeps the look consistent
                  across iOS and Android. The OSM tile policy asks for a
                  reasonable User-Agent and limited zoom; we honour both. */}
              <UrlTile
                urlTemplate="https://tile.openstreetmap.org/{z}/{x}/{y}.png"
                maximumZ={19}
                shouldReplaceMapContent={Platform.OS === 'ios'}
              />

              {/* Lidar outline (closed polygon traced via polyline). */}
              {polylinePoints.length > 2 && (
                <Polyline
                  coordinates={polylinePoints}
                  strokeColor="rgba(56,189,248,0.9)"
                  strokeWidth={2}
                  geodesic={false}
                />
              )}

              {/* Robot marker. Tinted to match the rest of the app. */}
              <Marker coordinate={origin} anchor={{ x: 0.5, y: 0.5 }}>
                <View style={styles.robotMarker}>
                  <View style={styles.robotMarkerDot} />
                </View>
              </Marker>
            </MapView>

            <View style={styles.overlay} pointerEvents="box-none">
              {(() => {
                // Build the status pill from the typed state machine.
                let dot = '#64748b';
                let label = 'Idle';
                if (!connected) {
                  dot = '#ef4444'; label = 'Disconnected';
                } else if (lidarStatus.kind === 'scanning' && scan) {
                  dot = '#22c55e'; label = `${scan.points.length} pts · scan #${scan.scanId}`;
                } else if (lidarStatus.kind === 'starting') {
                  dot = '#eab308'; label = 'Starting lidar…';
                } else if (lidarStatus.kind === 'error') {
                  dot = '#ef4444'; label = 'Lidar unavailable';
                }
                return (
                  <View style={styles.statusPill}>
                    <View style={[styles.statusDot, { backgroundColor: dot }]} />
                    <Text style={styles.statusText}>{label}</Text>
                  </View>
                );
              })()}

              <View style={styles.toolbar}>
                <TouchableOpacity style={styles.toolBtn} onPress={recenter} activeOpacity={0.85}>
                  <Ionicons name="locate" size={20} color="#0f172a" />
                </TouchableOpacity>
                <TouchableOpacity
                  style={styles.toolBtn}
                  onPress={() => setHeadingOffset(h => (h + 15) % 360)}
                  activeOpacity={0.85}
                >
                  <Ionicons name="compass" size={20} color="#0f172a" />
                  <Text style={styles.toolBtnText}>{headingOffset}°</Text>
                </TouchableOpacity>
              </View>

              {lidarStatus.kind === 'error' && (
                <View style={styles.errorBanner} pointerEvents="auto">
                  <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <Ionicons name="warning" size={20} color="#fbbf24" />
                    <Text style={styles.errorBannerTitle}>Lidar unavailable</Text>
                  </View>
                  <Text style={styles.errorBannerReason}>{lidarStatus.reason}</Text>
                  <Text style={styles.errorBannerHint}>
                    Plug the RPLidar in (USB) and tap Retry. The Pi will re-probe
                    the device — no need to restart the bridge.
                  </Text>
                  <TouchableOpacity
                    style={styles.errorBannerBtn}
                    onPress={() => conn && sendLidarOn(conn)}
                    activeOpacity={0.85}
                  >
                    <Ionicons name="refresh" size={16} color="#451a03" />
                    <Text style={styles.errorBannerBtnText}>Retry</Text>
                  </TouchableOpacity>
                </View>
              )}
            </View>
          </>
        )}
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a' },
  header: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingHorizontal: 16, paddingVertical: 14,
    borderBottomWidth: 1, borderBottomColor: '#1e293b',
  },
  title: { color: '#f1f5f9', fontSize: 20, fontWeight: '700' },
  headerBtn: { padding: 4 },

  map: { flex: 1 },

  overlay: {
    ...StyleSheet.absoluteFillObject,
    padding: 14, justifyContent: 'space-between', alignItems: 'flex-end',
  },
  statusPill: {
    flexDirection: 'row', alignItems: 'center', gap: 8,
    backgroundColor: 'rgba(15,23,42,0.92)', borderWidth: 1, borderColor: '#1e293b',
    paddingHorizontal: 12, paddingVertical: 8, borderRadius: 20,
    alignSelf: 'flex-start',
  },
  statusDot: { width: 8, height: 8, borderRadius: 4 },
  statusText: { color: '#e2e8f0', fontSize: 12, fontWeight: '600' },

  toolbar: { gap: 10 },
  toolBtn: {
    backgroundColor: '#38bdf8', borderRadius: 12,
    paddingHorizontal: 12, paddingVertical: 10,
    alignItems: 'center', justifyContent: 'center',
    flexDirection: 'row', gap: 6,
    shadowColor: '#000', shadowOpacity: 0.3, shadowRadius: 4, shadowOffset: { width: 0, height: 2 },
  },
  toolBtnText: { color: '#0f172a', fontWeight: '700', fontSize: 12 },

  errorBanner: {
    alignSelf: 'stretch',
    backgroundColor: '#451a03', borderColor: '#92400e', borderWidth: 1,
    padding: 14, borderRadius: 14, gap: 6,
    shadowColor: '#000', shadowOpacity: 0.45, shadowRadius: 8, shadowOffset: { width: 0, height: 4 },
  },
  errorBannerTitle: { color: '#fde68a', fontWeight: '700', fontSize: 15 },
  errorBannerReason: { color: '#fcd34d', fontSize: 13, lineHeight: 18 },
  errorBannerHint:   { color: '#fbbf24', fontSize: 12, lineHeight: 17, marginTop: 2 },
  errorBannerBtn: {
    alignSelf: 'flex-start', marginTop: 6,
    flexDirection: 'row', alignItems: 'center', gap: 6,
    backgroundColor: '#fbbf24', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8,
  },
  errorBannerBtnText: { color: '#451a03', fontWeight: '700', fontSize: 13 },

  robotMarker: {
    width: 22, height: 22, borderRadius: 11,
    backgroundColor: 'rgba(56,189,248,0.25)', alignItems: 'center', justifyContent: 'center',
    borderWidth: 2, borderColor: '#38bdf8',
  },
  robotMarkerDot: {
    width: 8, height: 8, borderRadius: 4, backgroundColor: '#0f172a',
  },

  errorBox: {
    margin: 20, padding: 18, gap: 10,
    backgroundColor: '#1e293b', borderRadius: 14,
    borderWidth: 1, borderColor: '#334155',
    alignItems: 'flex-start',
  },
  errorTitle: { color: '#f1f5f9', fontWeight: '700', fontSize: 16 },
  errorDetail: { color: '#94a3b8', fontSize: 13, lineHeight: 18 },
  actionBtn: {
    backgroundColor: '#38bdf8', paddingHorizontal: 14, paddingVertical: 10,
    borderRadius: 10, marginTop: 4,
  },
  actionText: { color: '#0f172a', fontWeight: '700', fontSize: 13 },

  loadingBox: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: 10 },
  loadingText: { color: '#94a3b8' },
});
