// Root: either showing the scan/pair screen OR the control screen,
// based on whether we have an active connection. No navigation library
// needed — this app has exactly two screens and one piece of state.

import React, { useState } from 'react';
import { StatusBar } from 'expo-status-bar';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import ScanScreen from './src/screens/ScanScreen';
import ControlScreen from './src/screens/ControlScreen';

export default function App() {
  const [paired, setPaired] = useState<{ id: string; name: string } | null>(null);

  return (
    <SafeAreaProvider>
      <StatusBar style="light" />
      {paired ? (
        <ControlScreen
          deviceId={paired.id}
          deviceName={paired.name}
          onDisconnected={() => setPaired(null)}
        />
      ) : (
        <ScanScreen onConnected={(id, name) => setPaired({ id, name })} />
      )}
    </SafeAreaProvider>
  );
}
