// OTA version panel + "check for updates" control.
//
// This deals ONLY with over-the-air (EAS Update) JS bundles — not App
// Store / native versions. It shows:
//   • the app version (from app.json — pure JS, travels with OTA bundles)
//   • Current OTA  — the bundle actually running right now
//   • Latest OTA   — what the update server offers for this runtimeVersion
//
// Flow: tap "Check for updates" → checkForUpdateAsync (OTA-only; expo-updates
// never touches the native binary). If a newer bundle exists, the Latest row
// fills in and the button becomes "Download & restart" → fetchUpdateAsync →
// reloadAsync. In a dev build updates are disabled, so we say so.

import React from 'react';
import { View, Text, TouchableOpacity, ActivityIndicator, StyleSheet, Alert } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import * as Updates from 'expo-updates';
import appJson from '../../app.json';

const APP_VERSION = appJson.expo.version;

// "019e7e93 · May 27, 1:59 PM" for an OTA bundle; null when there's no id.
function fmtOta(updateId?: string, createdAt?: Date): string | null {
  if (!updateId) return null;
  const short = updateId.slice(0, 8);
  if (!createdAt) return short;
  const when = createdAt.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
  return `${short} · ${when}`;
}

export default function UpdateFooter() {
  const {
    currentlyRunning,
    availableUpdate,
    isUpdateAvailable,
    isUpdatePending,
    isChecking,
    isDownloading,
    lastCheckForUpdateTimeSinceRestart,
    checkError,
    downloadError,
  } = Updates.useUpdates();

  const busy = isChecking || isDownloading;
  const hasChecked = !!lastCheckForUpdateTimeSinceRestart;

  // Current running bundle: an OTA id, or the JS embedded in the binary.
  const currentLabel = currentlyRunning.isEmbeddedLaunch
    ? 'embedded (shipped with build)'
    : fmtOta(currentlyRunning.updateId, currentlyRunning.createdAt) ?? 'embedded';

  // Latest available from the server.
  let latestLabel: string;
  if (!Updates.isEnabled) {
    latestLabel = 'disabled in dev build';
  } else if (busy) {
    latestLabel = isDownloading ? 'downloading…' : 'checking…';
  } else if (isUpdateAvailable && availableUpdate) {
    latestLabel = fmtOta(availableUpdate.updateId, availableUpdate.createdAt) ?? 'available';
  } else if (hasChecked) {
    latestLabel = 'up to date';
  } else {
    latestLabel = 'tap to check';
  }

  const onCheck = async () => {
    try {
      await Updates.checkForUpdateAsync();
    } catch (e: any) {
      Alert.alert('Check failed', e?.message ?? 'Could not reach the update server.');
    }
  };

  const onDownloadAndRestart = async () => {
    try {
      if (!isUpdatePending) await Updates.fetchUpdateAsync();
      await Updates.reloadAsync();
    } catch (e: any) {
      Alert.alert('Update failed', e?.message ?? 'Could not download the update.');
    }
  };

  // Pick the primary action by state.
  const readyToInstall = isUpdatePending || (isUpdateAvailable && !busy);

  return (
    <View style={styles.wrap}>
      <Text style={styles.appVersion}>Robot Control v{APP_VERSION}</Text>

      <View style={styles.row}>
        <Text style={styles.label}>Current OTA</Text>
        <Text style={styles.value}>{currentLabel}</Text>
      </View>
      <View style={styles.row}>
        <Text style={styles.label}>Latest OTA</Text>
        <Text style={[styles.value, isUpdateAvailable && styles.valueNew]}>{latestLabel}</Text>
      </View>

      {(checkError || downloadError) && (
        <Text style={styles.err}>{(downloadError ?? checkError)?.message}</Text>
      )}

      <TouchableOpacity
        style={[styles.btn, readyToInstall && styles.btnPrimary, busy && { opacity: 0.6 }]}
        onPress={readyToInstall ? onDownloadAndRestart : onCheck}
        disabled={busy || !Updates.isEnabled}
        activeOpacity={0.7}
      >
        {busy ? (
          <ActivityIndicator size="small" color={readyToInstall ? '#0f172a' : '#94a3b8'} />
        ) : (
          <Ionicons
            name={readyToInstall ? 'cloud-download' : 'refresh'}
            size={15}
            color={readyToInstall ? '#0f172a' : '#94a3b8'}
          />
        )}
        <Text style={[styles.btnText, readyToInstall && styles.btnTextPrimary]}>
          {!Updates.isEnabled ? 'Updates unavailable'
            : isDownloading ? 'Downloading…'
            : isChecking ? 'Checking…'
            : isUpdatePending ? 'Restart to apply'
            : isUpdateAvailable ? 'Download & restart'
            : 'Check for updates'}
        </Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignItems: 'stretch', paddingTop: 12, paddingBottom: 4, gap: 6 },
  appVersion: { color: '#64748b', fontSize: 12, fontWeight: '700', textAlign: 'center', marginBottom: 2 },
  row: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' },
  label: { color: '#64748b', fontSize: 12 },
  value: { color: '#94a3b8', fontSize: 12, fontWeight: '600' },
  valueNew: { color: '#38bdf8' },
  err: { color: '#f87171', fontSize: 11, marginTop: 2 },
  btn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6,
    paddingVertical: 8, marginTop: 6, borderRadius: 10,
    borderWidth: 1, borderColor: '#334155',
  },
  btnPrimary: { backgroundColor: '#38bdf8', borderColor: '#38bdf8' },
  btnText: { color: '#94a3b8', fontSize: 13, fontWeight: '600' },
  btnTextPrimary: { color: '#0f172a', fontWeight: '700' },
});
