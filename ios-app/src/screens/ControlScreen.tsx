// Tank-control pad + live video + photo capture.
//
//   • Five tank buttons map 1:1 to the BLE NUS commands the Pi-side
//     ble_server.py writes directly to the Arduino over USB serial:
//       UP / DOWN / LEFT / RIGHT / STOP
//   • A live video panel up top shows the latest frame streamed over
//     the video BLE characteristic (see ../lib/videoFrames.ts for the
//     wire format).
//   • The camera button asks the Pi for a high-res photo (a one-shot
//     PHOTO command — same wire format, photo flag set), then ships
//     the JPEG to the Claude API for a grass-health verdict.
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
  Image, Modal, ActivityIndicator, ScrollView, TextInput,
  AppState, AppStateStatus, Linking,
} from 'react-native';
import * as Haptics from 'expo-haptics';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Ionicons } from '@expo/vector-icons';
import { connect, RobotConnection, describeBleError, BleProblem } from '../lib/ble';
import { CompleteFrame } from '../lib/videoFrames';
import {
  assessGrassHealth, getApiKey, setApiKey, GrassAssessment,
} from '../lib/claude';
import {
  QualityPreset, PRESETS, getQualityPreset, setQualityPreset, settingsFor, DEFAULT_PRESET,
} from '../lib/videoQuality';
import MapModal from './MapModal';
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

type PhotoState =
  | { kind: 'idle' }
  | { kind: 'waiting' }                                  // PHOTO sent, awaiting BLE frame
  | { kind: 'analyzing'; jpegBase64: string }
  | { kind: 'done'; jpegBase64: string; result: GrassAssessment }
  | { kind: 'error'; jpegBase64?: string; message: string };

// If the Pi doesn't send a photo back within this window, give up so the
// user can try again instead of staring at a spinner.
const PHOTO_TIMEOUT_MS = 15000;

// If we're connected and the live video has been silent for this long,
// the panel switches to a "Stream paused" overlay so the user isn't
// staring at a stale frame.  Just under 2× the Low preset's frame period
// (3 fps = ~330 ms) so a single dropped frame doesn't trigger it.
const VIDEO_STALE_MS = 4000;

export default function ControlScreen({ deviceId, deviceName, onDisconnected }: Props) {
  const connRef = useRef<RobotConnection | null>(null);
  const [status, setStatus] = useState<'connecting' | 'connected' | 'reconnecting' | 'disconnected'>('connecting');
  const [lastReply, setLastReply] = useState<string>('');
  const [activeKey, setActiveKey] = useState<string | null>(null);
  const repeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttemptRef = useRef<number>(0);
  const cancelledRef = useRef<boolean>(false);

  const [latestFrame, setLatestFrame] = useState<string | null>(null);  // base64 jpeg
  const [photoState, setPhotoState] = useState<PhotoState>({ kind: 'idle' });
  const photoTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Need a ref because the BLE frame callback closure captures the value
  // at subscription time; the state setter won't see the latest UI state.
  const photoStateRef = useRef<PhotoState>(photoState);
  photoStateRef.current = photoState;

  // Stream-health state.  hasVideoChar = the connected Pi exposes the
  // video characteristic (false on older firmware); videoStale = we are
  // connected but no frame has arrived for VIDEO_STALE_MS so the preview
  // is probably dead.
  const [hasVideoChar, setHasVideoChar] = useState(true);
  const [videoStale, setVideoStale] = useState(false);
  const lastFrameAtRef = useRef<number>(0);
  const staleTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Last hard failure we got from the BLE stack, surfaced as a banner so
  // the user knows what to do.  Cleared the moment we reconnect.
  const [problem, setProblem] = useState<BleProblem | null>(null);

  const [showSettings, setShowSettings] = useState(false);
  const [showMap, setShowMap] = useState(false);
  const [hasApiKey, setHasApiKey] = useState(false);
  const [quality, setQuality] = useState<QualityPreset>(DEFAULT_PRESET);
  // Camera diagnostics — populated from CAMINFO replies and CAM:<n> acks
  // that the Pi sends back on the TX channel.  Surfaced in the Settings
  // modal so the user can see which port is currently active and whether
  // the Pi sees a sensor at all.
  const [cameraInfo, setCameraInfo] = useState<string>('');
  const [cameraPort, setCameraPort] = useState<number | null>(null);

  useEffect(() => {
    getApiKey().then(k => setHasApiKey(!!k));
    getQualityPreset().then(setQuality);
  }, [showSettings]);

  // Send CAM:<n> to the Pi to switch which CSI port libcamera opens.
  // Briefly interrupts the live stream while picamera2 re-opens — clear
  // the staleness clock so we don't false-alarm during the swap.
  const changeCameraPort = async (n: number) => {
    const conn = connRef.current;
    if (!conn) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    try { await conn.setCameraPort(n); } catch {}
    lastFrameAtRef.current = Date.now();
    setVideoStale(false);
  };

  // Ask the Pi for the list of detected cameras + active port.  The
  // reply lands on the TX channel and is parsed by the onReply handler
  // above, which writes into cameraInfo/cameraPort.
  const fetchCameraInfo = async () => {
    const conn = connRef.current;
    if (!conn) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {});
    try { await conn.requestCameraInfo(); } catch {}
  };

  // Apply a quality preset: persist it, push it to the Pi if connected.
  const changeQuality = async (p: QualityPreset) => {
    setQuality(p);
    await setQualityPreset(p);
    const conn = connRef.current;
    if (conn && conn.hasVideo) {
      try { await conn.applyQuality(settingsFor(p)); } catch {}
      // Reset the staleness timer — a resolution change interrupts the
      // stream briefly, and we don't want a false alarm.
      lastFrameAtRef.current = Date.now();
      setVideoStale(false);
    }
  };

  // Pause/resume the stream from the Pi when the app moves to/from
  // background.  No point burning the camera while the user can't see it.
  useEffect(() => {
    const onChange = (s: AppStateStatus) => {
      const conn = connRef.current;
      if (!conn) return;
      if (s === 'active') {
        conn.setStreaming(true).catch(() => {});
        // Belt-and-braces: a long background can drop the link silently.
        // The onDisconnected listener should fire anyway, but force a
        // fresh staleness clock so the user gets accurate feedback.
        lastFrameAtRef.current = Date.now();
      } else if (s === 'background' || s === 'inactive') {
        conn.setStreaming(false).catch(() => {});
        // Cancel any in-flight repeat so we don't drive while in pocket.
        if (repeatTimerRef.current) {
          clearInterval(repeatTimerRef.current);
          repeatTimerRef.current = null;
        }
        // And explicitly stop the robot — being backgrounded is a
        // strong "user is no longer driving" signal.
        conn.send('STOP').catch(() => {});
      }
    };
    const sub = AppState.addEventListener('change', onChange);
    return () => sub.remove();
  }, []);

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
        setProblem(null);
        setHasVideoChar(conn.hasVideo);
        lastFrameAtRef.current = Date.now();
        setVideoStale(false);
        conn.onReply((line) => {
          setLastReply(line);
          // Pick out camera diagnostics so the Settings modal can show
          // "Active port: 0, 2 cameras detected, …" without forcing the
          // user to read the raw reply line.
          const camInfoMatch = line.match(/^OK:\s*CAMINFO\s+(.+)$/i);
          if (camInfoMatch) {
            setCameraInfo(camInfoMatch[1]);
            const active = camInfoMatch[1].match(/active=(\d+)/i);
            if (active) setCameraPort(parseInt(active[1], 10));
            else if (/active=default/i.test(camInfoMatch[1])) setCameraPort(null);
            return;
          }
          const camAckMatch = line.match(/^OK:\s*CAM=(\d+)/i);
          if (camAckMatch) setCameraPort(parseInt(camAckMatch[1], 10));
        });
        conn.onFrame((frame: CompleteFrame) => {
          // Record the moment of arrival so the staleness watchdog can
          // tell live video from a frozen last frame.
          lastFrameAtRef.current = Date.now();
          if (videoStale) setVideoStale(false);
          // Always update the live preview…
          if (!frame.isPhoto) {
            setLatestFrame(frame.jpegBase64);
            return;
          }
          // …and if a photo capture is in flight, this is the response.
          const cur = photoStateRef.current;
          if (cur.kind === 'waiting') {
            if (photoTimeoutRef.current) {
              clearTimeout(photoTimeoutRef.current);
              photoTimeoutRef.current = null;
            }
            setPhotoState({ kind: 'analyzing', jpegBase64: frame.jpegBase64 });
            runAssessment(frame.jpegBase64);
          } else {
            // Unsolicited photo frame (e.g. user double-tapped) — just
            // refresh the preview so the bigger image isn't wasted.
            setLatestFrame(frame.jpegBase64);
          }
        });
        conn.device.onDisconnected(() => {
          // Pi side fires a safety STOP on disconnect already (ble_server.py
          // listens to BlueZ's PropertiesChanged), so the robot is already
          // halted — our job here is to claw the connection back.
          connRef.current = null;
          scheduleReconnect();
        });

        // Push the user's preferred quality at connect time.  Fire-and-
        // forget — the Pi acks each line on the TX channel so a failure
        // here just means a quality knob silently didn't take, not that
        // control is broken.
        if (conn.hasVideo) {
          const preset = await getQualityPreset();
          setQuality(preset);
          conn.applyQuality(settingsFor(preset)).catch(() => {});
        }
      } catch (e: any) {
        // eslint-disable-next-line no-console
        console.warn('[ble] connect failed', e?.message);
        setProblem(describeBleError(e));
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
        if (!problem) {
          setProblem({
            title: 'Lost connection to the robot',
            detail: 'We tried six times to reconnect and gave up. Make sure the robot is powered on and within Bluetooth range.',
            action: 'retry',
          });
        }
        return;
      }
      const delay = RECONNECT_DELAYS_MS[Math.min(n, RECONNECT_DELAYS_MS.length - 1)];
      reconnectAttemptRef.current = n + 1;
      setStatus('reconnecting');
      reconnectTimerRef.current = setTimeout(() => attemptConnect(true), delay);
    };

    attemptConnect(false);

    // Video staleness watchdog.  Every second while connected, check
    // whether the last frame is older than VIDEO_STALE_MS.  Catches
    // mid-connection BLE wedges that don't surface as a hard disconnect.
    staleTimerRef.current = setInterval(() => {
      if (!connRef.current || !connRef.current.hasVideo) return;
      const since = Date.now() - lastFrameAtRef.current;
      setVideoStale(prev => {
        const next = since > VIDEO_STALE_MS;
        return prev === next ? prev : next;
      });
    }, 1000);

    return () => {
      cancelledRef.current = true;
      if (repeatTimerRef.current) clearInterval(repeatTimerRef.current);
      if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
      if (photoTimeoutRef.current) clearTimeout(photoTimeoutRef.current);
      if (staleTimerRef.current) clearInterval(staleTimerRef.current);
      // Best-effort stop + cleanup. Fire-and-forget; nothing to await on unmount.
      const conn = connRef.current;
      if (conn) {
        conn.send('STOP').catch(() => {});
        conn.disconnect().catch(() => {});
      }
    };
    // problem intentionally excluded — we read it inside scheduleReconnect
    // but don't want a fresh problem to retrigger the whole connect effect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    // Pi-side disconnect handler also fires STOP if BLE drops.
  };

  const runAssessment = async (jpegBase64: string) => {
    try {
      const result = await assessGrassHealth(jpegBase64);
      setPhotoState({ kind: 'done', jpegBase64, result });
    } catch (e: any) {
      setPhotoState({ kind: 'error', jpegBase64, message: e?.message || 'Analysis failed.' });
    }
  };

  const handlePhotoPress = async () => {
    const conn = connRef.current;
    if (!conn || status !== 'connected') return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});

    if (!conn.hasVideo) {
      setPhotoState({
        kind: 'error',
        message: 'This robot is on older firmware that does not support the camera. Update scripts/ble_server.py on the Pi and restart it.',
      });
      return;
    }

    const key = await getApiKey();
    if (!key) {
      setShowSettings(true);
      return;
    }

    setPhotoState({ kind: 'waiting' });
    try {
      await conn.requestPhoto();
    } catch (e: any) {
      setPhotoState({ kind: 'error', message: e?.message || 'PHOTO command failed.' });
      return;
    }
    if (photoTimeoutRef.current) clearTimeout(photoTimeoutRef.current);
    photoTimeoutRef.current = setTimeout(() => {
      if (photoStateRef.current.kind === 'waiting') {
        setPhotoState({
          kind: 'error',
          message:
            'The robot did not send a photo within 15 seconds. The Pi camera may not be installed, or the IMX519 overlay is not enabled. Run STATUS or check the Pi logs.',
        });
      }
    }, PHOTO_TIMEOUT_MS);
  };

  // Trigger a fresh connect attempt without unmounting the screen — used
  // by the "Retry" button when we've exhausted the reconnect ladder.
  const retryConnection = () => {
    reconnectAttemptRef.current = 0;
    setProblem(null);
    setStatus('connecting');
    // Bounce the effect by toggling the deviceId-keyed state.  Easiest
    // is to call onDisconnected() so ScanScreen takes over the
    // reconnect — that path is well-tested.
    onDisconnected();
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
        <TouchableOpacity
          onPress={() => setShowMap(true)}
          style={styles.headerIcon}
          disabled={status !== 'connected'}
        >
          <Ionicons
            name="map-outline"
            size={26}
            color={status === 'connected' ? '#38bdf8' : '#475569'}
          />
        </TouchableOpacity>
        <TouchableOpacity onPress={() => setShowSettings(true)} style={styles.headerIcon}>
          <Ionicons name="settings-outline" size={26} color="#94a3b8" />
        </TouchableOpacity>
        <TouchableOpacity onPress={handleForget} style={styles.headerIcon}>
          <Ionicons name="close-circle" size={28} color="#94a3b8" />
        </TouchableOpacity>
      </View>

      {problem && status !== 'connected' && (
        <ProblemBanner
          problem={problem}
          onRetry={retryConnection}
          onDismiss={() => setProblem(null)}
        />
      )}

      {status === 'disconnected' && !problem && (
        <TouchableOpacity
          style={styles.reconnectBtn}
          onPress={() => onDisconnected()}
          activeOpacity={0.85}
        >
          <Ionicons name="refresh" size={18} color="#0f172a" />
          <Text style={styles.reconnectText}>Back to pairing</Text>
        </TouchableOpacity>
      )}

      <View style={styles.videoFrame}>
        {latestFrame ? (
          <Image
            source={{ uri: `data:image/jpeg;base64,${latestFrame}` }}
            style={styles.videoImg}
            resizeMode="cover"
          />
        ) : (
          <View style={styles.videoPlaceholder}>
            <Ionicons name="videocam-off-outline" size={40} color="#475569" />
            <Text style={styles.videoPlaceholderText}>
              {!hasVideoChar
                ? 'Camera not available on this robot.\nUpdate the Pi script to enable video.'
                : status === 'connected' ? 'Waiting for video…'
                : 'Live video (BLE)'}
            </Text>
          </View>
        )}
        {/* Staleness overlay: connected, hasVideo, but no recent frame.
            Renders on top of the last-good frame so the user knows the
            preview is frozen rather than just slow. */}
        {status === 'connected' && hasVideoChar && videoStale && (
          <View style={styles.videoOverlay} pointerEvents="none">
            <Ionicons name="cloud-offline-outline" size={32} color="#fde68a" />
            <Text style={styles.videoOverlayText}>
              Stream paused — radio quiet for a few seconds. Move closer or lower the quality in Settings.
            </Text>
          </View>
        )}
        <TouchableOpacity
          style={[
            styles.cameraBtn,
            (!hasVideoChar || status !== 'connected') && { backgroundColor: '#334155' },
          ]}
          onPress={handlePhotoPress}
          activeOpacity={0.85}
          disabled={status !== 'connected' || !hasVideoChar || photoState.kind === 'waiting' || photoState.kind === 'analyzing'}
        >
          <Ionicons
            name="camera"
            size={26}
            color={status === 'connected' && hasVideoChar ? '#0f172a' : '#64748b'}
          />
        </TouchableOpacity>
      </View>

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

      <PhotoModal
        state={photoState}
        onClose={() => setPhotoState({ kind: 'idle' })}
        onOpenSettings={() => { setPhotoState({ kind: 'idle' }); setShowSettings(true); }}
      />

      <SettingsModal
        visible={showSettings}
        hasApiKey={hasApiKey}
        quality={quality}
        onChangeQuality={changeQuality}
        connected={status === 'connected'}
        cameraPort={cameraPort}
        cameraInfo={cameraInfo}
        onChangeCameraPort={changeCameraPort}
        onFetchCameraInfo={fetchCameraInfo}
        onClose={() => setShowSettings(false)}
      />

      <MapModal
        visible={showMap}
        conn={connRef.current}
        connected={status === 'connected'}
        onClose={() => setShowMap(false)}
      />
    </View>
  );
}

// Inline banner that surfaces a BLE-level failure with an action button.
// Sits above the video frame so the user sees it without scrolling.
function ProblemBanner({
  problem, onRetry, onDismiss,
}: {
  problem: BleProblem;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const openSettings = () => {
    Linking.openURL('app-settings:').catch(() => {
      Alert.alert(problem.title, problem.detail);
    });
  };
  const actionLabel =
    problem.action === 'open-settings' ? 'Open iOS Settings'
    : problem.action === 'retry' ? 'Retry'
    : problem.action === 'rescan' ? 'Rescan'
    : null;
  const onAction =
    problem.action === 'open-settings' ? openSettings
    : problem.action === 'retry' ? onRetry
    : problem.action === 'rescan' ? onRetry
    : undefined;
  return (
    <View style={styles.problemBanner}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Ionicons name="warning" size={18} color="#fbbf24" />
        <Text style={styles.problemTitle}>{problem.title}</Text>
        <TouchableOpacity onPress={onDismiss} hitSlop={10} style={{ marginLeft: 'auto' }}>
          <Ionicons name="close" size={18} color="#fbbf24" />
        </TouchableOpacity>
      </View>
      <Text style={styles.problemDetail}>{problem.detail}</Text>
      {actionLabel && onAction && (
        <TouchableOpacity style={styles.problemActionBtn} onPress={onAction} activeOpacity={0.85}>
          <Text style={styles.problemActionText}>{actionLabel}</Text>
        </TouchableOpacity>
      )}
    </View>
  );
}

function PhotoModal({
  state, onClose, onOpenSettings,
}: {
  state: PhotoState;
  onClose: () => void;
  onOpenSettings: () => void;
}) {
  const visible = state.kind !== 'idle';
  const jpeg =
    state.kind === 'analyzing' ? state.jpegBase64
    : state.kind === 'done' ? state.jpegBase64
    : state.kind === 'error' ? state.jpegBase64
    : undefined;

  const showApiKeyHint = state.kind === 'error' && /api key/i.test(state.message);

  return (
    <Modal animationType="slide" transparent visible={visible} onRequestClose={onClose}>
      <View style={styles.modalBackdrop}>
        <View style={styles.modalCard}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Lawn check</Text>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <Ionicons name="close" size={24} color="#94a3b8" />
            </TouchableOpacity>
          </View>

          {jpeg && (
            <Image
              source={{ uri: `data:image/jpeg;base64,${jpeg}` }}
              style={styles.modalImg}
              resizeMode="cover"
            />
          )}

          {state.kind === 'waiting' && (
            <View style={styles.modalBody}>
              <ActivityIndicator color="#38bdf8" />
              <Text style={styles.modalStatus}>Asking the robot for a photo…</Text>
            </View>
          )}

          {state.kind === 'analyzing' && (
            <View style={styles.modalBody}>
              <ActivityIndicator color="#38bdf8" />
              <Text style={styles.modalStatus}>Sending to Claude for assessment…</Text>
            </View>
          )}

          {state.kind === 'done' && (
            <ScrollView style={styles.modalScroll} contentContainerStyle={{ paddingBottom: 16 }}>
              <HealthReport score={state.result.score} summary={state.result.summary} />
            </ScrollView>
          )}

          {state.kind === 'error' && (
            <View style={styles.modalBody}>
              <Ionicons name="alert-circle" size={28} color="#f97316" />
              <Text style={styles.modalStatus}>{state.message}</Text>
              {showApiKeyHint && (
                <TouchableOpacity style={styles.modalLinkBtn} onPress={onOpenSettings}>
                  <Text style={styles.modalLinkText}>Open Settings</Text>
                </TouchableOpacity>
              )}
            </View>
          )}
        </View>
      </View>
    </Modal>
  );
}

// Health buckets, per spec:
//   0–30   red    (mostly dead / heavily damaged)
//   31–75  yellow (stressed / patchy)
//   76–100 green  (healthy)
function bucketForScore(score: number): { label: string; color: string } {
  if (score <= 30) return { label: 'Poor',  color: '#ef4444' };
  if (score <= 75) return { label: 'Fair',  color: '#eab308' };
  return { label: 'Healthy', color: '#22c55e' };
}

function HealthReport({ score, summary }: { score: number | null; summary: string }) {
  if (score == null) {
    return (
      <View>
        <View style={styles.scoreRow}>
          <Text style={styles.scoreLabel}>Lawn health</Text>
          <Text style={[styles.scoreValue, { color: '#94a3b8' }]}>n/a</Text>
        </View>
        <Text style={styles.modalSummary}>{summary}</Text>
      </View>
    );
  }

  const clamped = Math.max(0, Math.min(100, Math.round(score)));
  const bucket = bucketForScore(clamped);

  return (
    <View>
      <View style={styles.scoreRow}>
        <Text style={styles.scoreLabel}>Lawn health</Text>
        <Text style={[styles.scoreValue, { color: bucket.color }]}>{clamped}%</Text>
      </View>
      <View style={styles.healthBarTrack}>
        <View
          style={[
            styles.healthBarFill,
            { width: `${clamped}%`, backgroundColor: bucket.color },
          ]}
        />
      </View>
      <Text style={[styles.bucketLabel, { color: bucket.color }]}>{bucket.label}</Text>
      <Text style={styles.modalSummary}>{summary}</Text>
    </View>
  );
}

function SettingsModal({
  visible, hasApiKey, quality, onChangeQuality,
  connected, cameraPort, cameraInfo, onChangeCameraPort, onFetchCameraInfo,
  onClose,
}: {
  visible: boolean;
  hasApiKey: boolean;
  quality: QualityPreset;
  onChangeQuality: (p: QualityPreset) => void;
  connected: boolean;
  cameraPort: number | null;
  cameraInfo: string;
  onChangeCameraPort: (n: number) => void;
  onFetchCameraInfo: () => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!visible) setDraft('');
  }, [visible]);

  const save = async () => {
    if (!draft.trim()) return;
    setSaving(true);
    try {
      await setApiKey(draft);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal animationType="slide" transparent visible={visible} onRequestClose={onClose}>
      <View style={styles.modalBackdrop}>
        <View style={styles.modalCard}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Settings</Text>
            <TouchableOpacity onPress={onClose} hitSlop={10}>
              <Ionicons name="close" size={24} color="#94a3b8" />
            </TouchableOpacity>
          </View>
          <ScrollView contentContainerStyle={{ paddingBottom: 8 }}>
            <View style={styles.modalBody}>
              <Text style={styles.settingsLabel}>Video quality</Text>
              <Text style={styles.settingsHint}>
                Higher quality looks sharper but needs more BLE bandwidth.
                Drop to Low if the stream stalls or stutters.
              </Text>
              <View style={styles.qualityRow}>
                {(['low', 'medium', 'high'] as QualityPreset[]).map(p => {
                  const s = PRESETS[p];
                  const active = p === quality;
                  return (
                    <TouchableOpacity
                      key={p}
                      style={[styles.qualityBtn, active && styles.qualityBtnActive]}
                      onPress={() => onChangeQuality(p)}
                      activeOpacity={0.85}
                    >
                      <Text style={[styles.qualityLabel, active && styles.qualityLabelActive]}>
                        {s.label}
                      </Text>
                      <Text style={styles.qualityMeta}>
                        {s.width}×{s.height} · {s.fps} fps
                      </Text>
                    </TouchableOpacity>
                  );
                })}
              </View>
              <Text style={[styles.settingsHint, { marginTop: -4 }]}>
                {PRESETS[quality].hint}
              </Text>

              <View style={styles.settingsDivider} />

              <Text style={styles.settingsLabel}>Camera port</Text>
              <Text style={styles.settingsHint}>
                The Pi 5 has two CSI camera ports. If video is black or the
                Pi reports the camera as off, the ribbon may be in the
                other port — toggle here to switch live.
              </Text>
              <View style={styles.qualityRow}>
                {[0, 1].map(n => {
                  const active = cameraPort === n;
                  return (
                    <TouchableOpacity
                      key={n}
                      style={[
                        styles.qualityBtn,
                        active && styles.qualityBtnActive,
                        !connected && { opacity: 0.4 },
                      ]}
                      onPress={() => onChangeCameraPort(n)}
                      disabled={!connected}
                      activeOpacity={0.85}
                    >
                      <Text style={[styles.qualityLabel, active && styles.qualityLabelActive]}>
                        Port {n}
                      </Text>
                      <Text style={styles.qualityMeta}>
                        {active ? 'active' : 'CAM:' + n}
                      </Text>
                    </TouchableOpacity>
                  );
                })}
                <TouchableOpacity
                  style={[styles.qualityBtn, !connected && { opacity: 0.4 }]}
                  onPress={onFetchCameraInfo}
                  disabled={!connected}
                  activeOpacity={0.85}
                >
                  <Text style={styles.qualityLabel}>Info</Text>
                  <Text style={styles.qualityMeta}>CAMINFO</Text>
                </TouchableOpacity>
              </View>
              {cameraInfo ? (
                <Text style={styles.cameraInfoText}>{cameraInfo}</Text>
              ) : (
                <Text style={styles.settingsHint}>
                  Press <Text style={{ color: '#cbd5e1' }}>Info</Text> to ask the Pi which sensors libcamera can see.
                </Text>
              )}

              <View style={styles.settingsDivider} />

              <Text style={styles.settingsLabel}>Anthropic API key</Text>
              <Text style={styles.settingsHint}>
                Used by the lawn-check button to call Claude for a verdict.
                Stored locally on this device.
                {hasApiKey ? ' A key is already saved — entering a new one replaces it.' : ''}
              </Text>
              <TextInput
                style={styles.input}
                placeholder="sk-ant-…"
                placeholderTextColor="#475569"
                value={draft}
                onChangeText={setDraft}
                autoCapitalize="none"
                autoCorrect={false}
                secureTextEntry
              />
              <TouchableOpacity
                style={[styles.saveBtn, (!draft.trim() || saving) && { opacity: 0.5 }]}
                onPress={save}
                disabled={!draft.trim() || saving}
              >
                <Text style={styles.saveText}>{saving ? 'Saving…' : 'Save key'}</Text>
              </TouchableOpacity>
            </View>
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#0f172a', padding: 16, paddingTop: Platform.OS === 'ios' ? 60 : 24 },
  header: { flexDirection: 'row', alignItems: 'center', marginBottom: 12 },
  title: { color: '#f1f5f9', fontSize: 22, fontWeight: '700' },
  subtitle: { color: '#94a3b8', fontSize: 13, marginTop: 2 },
  headerIcon: { padding: 6, marginLeft: 4 },
  reconnectBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8,
    backgroundColor: '#38bdf8', borderRadius: 12, paddingVertical: 12, marginBottom: 12,
  },
  reconnectText: { color: '#0f172a', fontWeight: '700', fontSize: 14 },

  videoFrame: {
    aspectRatio: 4 / 3,
    backgroundColor: '#020617',
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#1e293b',
    overflow: 'hidden',
    marginBottom: 12,
  },
  videoImg: { width: '100%', height: '100%' },
  videoPlaceholder: {
    flex: 1, alignItems: 'center', justifyContent: 'center', gap: 8,
  },
  videoPlaceholderText: { color: '#64748b', fontSize: 13 },
  cameraBtn: {
    position: 'absolute', right: 12, bottom: 12,
    width: 52, height: 52, borderRadius: 26,
    backgroundColor: '#38bdf8', alignItems: 'center', justifyContent: 'center',
    shadowColor: '#000', shadowOpacity: 0.4, shadowRadius: 6, shadowOffset: { width: 0, height: 2 },
  },

  padWrap: { flex: 1, justifyContent: 'center', gap: 12 },
  padRow: { flexDirection: 'row', justifyContent: 'center', gap: 12 },
  padBtn: {
    width: 92, height: 92, borderRadius: 18,
    backgroundColor: '#1e293b', borderWidth: 1, borderColor: '#334155',
    alignItems: 'center', justifyContent: 'center',
  },
  padBtnActive: { backgroundColor: '#0369a1', borderColor: '#38bdf8' },
  padSpacer: { width: 92 },
  stopBtn: {
    width: 92, height: 92, borderRadius: 46,
    backgroundColor: '#dc2626', alignItems: 'center', justifyContent: 'center',
    borderWidth: 2, borderColor: '#fca5a5',
  },
  stopText: { color: '#fff', fontWeight: '900', fontSize: 18, letterSpacing: 1 },
  replyBox: { borderTopWidth: 1, borderTopColor: '#1e293b', paddingTop: 12, marginTop: 12 },
  replyLabel: { color: '#475569', fontSize: 10, letterSpacing: 1.5, marginBottom: 4 },
  replyText: { color: '#cbd5e1', fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace', fontSize: 13 },

  // Modal — shared layout for photo result + settings.
  modalBackdrop: {
    flex: 1, backgroundColor: 'rgba(2,6,23,0.75)',
    justifyContent: 'flex-end',
  },
  modalCard: {
    backgroundColor: '#0f172a', borderTopLeftRadius: 20, borderTopRightRadius: 20,
    borderTopWidth: 1, borderColor: '#1e293b',
    paddingHorizontal: 18, paddingTop: 14, paddingBottom: 24,
    maxHeight: '88%',
  },
  modalHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 },
  modalTitle: { color: '#f1f5f9', fontSize: 18, fontWeight: '700' },
  modalImg: { width: '100%', aspectRatio: 4 / 3, borderRadius: 12, marginBottom: 12, backgroundColor: '#020617' },
  modalBody: { paddingVertical: 18, alignItems: 'center', gap: 10 },
  modalScroll: { maxHeight: 240 },
  modalStatus: { color: '#cbd5e1', fontSize: 14, textAlign: 'center', paddingHorizontal: 8 },
  modalLinkBtn: { marginTop: 6, paddingVertical: 8, paddingHorizontal: 14, backgroundColor: '#1e293b', borderRadius: 10 },
  modalLinkText: { color: '#38bdf8', fontWeight: '600' },
  scoreRow: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: 8 },
  scoreLabel: { color: '#94a3b8', fontSize: 12, letterSpacing: 1.5, textTransform: 'uppercase' },
  scoreValue: { fontSize: 30, fontWeight: '800' },
  healthBarTrack: {
    height: 10, borderRadius: 5, backgroundColor: '#1e293b',
    overflow: 'hidden', marginBottom: 6,
  },
  healthBarFill: { height: '100%', borderRadius: 5 },
  bucketLabel: { fontSize: 13, fontWeight: '700', marginBottom: 10, letterSpacing: 0.5 },
  modalSummary: { color: '#e2e8f0', fontSize: 15, lineHeight: 21 },

  settingsLabel: { color: '#f1f5f9', fontSize: 14, fontWeight: '600', alignSelf: 'stretch' },
  settingsHint: { color: '#94a3b8', fontSize: 12, lineHeight: 17, alignSelf: 'stretch' },
  input: {
    alignSelf: 'stretch',
    backgroundColor: '#020617', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10,
    color: '#e2e8f0', fontSize: 14,
  },
  saveBtn: {
    alignSelf: 'stretch',
    backgroundColor: '#38bdf8', borderRadius: 12, paddingVertical: 12,
    alignItems: 'center', marginTop: 4,
  },
  saveText: { color: '#0f172a', fontWeight: '700', fontSize: 14 },

  qualityRow: { flexDirection: 'row', gap: 8, alignSelf: 'stretch' },
  qualityBtn: {
    flex: 1, paddingVertical: 10, paddingHorizontal: 8,
    backgroundColor: '#020617', borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 10, alignItems: 'center',
  },
  qualityBtnActive: { borderColor: '#38bdf8', backgroundColor: '#0c2237' },
  qualityLabel: { color: '#cbd5e1', fontWeight: '700', fontSize: 14 },
  qualityLabelActive: { color: '#38bdf8' },
  qualityMeta: { color: '#64748b', fontSize: 11, marginTop: 3 },
  settingsDivider: { height: 1, backgroundColor: '#1e293b', alignSelf: 'stretch', marginVertical: 8 },
  cameraInfoText: {
    alignSelf: 'stretch',
    color: '#86efac', fontSize: 11, lineHeight: 16,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
    backgroundColor: '#020617',
    borderWidth: 1, borderColor: '#1e293b',
    borderRadius: 8, paddingHorizontal: 10, paddingVertical: 8,
  },

  videoOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(2,6,23,0.55)',
    alignItems: 'center', justifyContent: 'center', gap: 8,
    paddingHorizontal: 24,
  },
  videoOverlayText: {
    color: '#fde68a', fontSize: 12, textAlign: 'center', lineHeight: 17,
  },

  problemBanner: {
    backgroundColor: '#451a03',
    borderWidth: 1, borderColor: '#92400e',
    borderRadius: 12, padding: 12, marginBottom: 12, gap: 6,
  },
  problemTitle: { color: '#fde68a', fontWeight: '700', fontSize: 14 },
  problemDetail: { color: '#fcd34d', fontSize: 12, lineHeight: 17 },
  problemActionBtn: {
    alignSelf: 'flex-start', marginTop: 4,
    backgroundColor: '#fbbf24', paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8,
  },
  problemActionText: { color: '#451a03', fontWeight: '700', fontSize: 13 },
});
