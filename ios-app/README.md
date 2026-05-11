# Robot Control — iOS BLE controller

An iPhone app that pairs with the robot over Bluetooth LE and drives the
tank with an on-screen pad. Ships to the same Apple Developer team
(`ZYH6M3S4ZF`, mperry@northernsoftwareconsulting.com) as AislePrompt,
using the same EAS / App Store Connect API key path.

## What it does

1. **Pair** — scans for any peripheral advertising the Nordic UART
   Service (`6E400001-…`), shows the list, taps one to connect. Saves
   the device id locally so the next launch auto-reconnects.
2. **Control** — 5-button pad (↑ ↓ ← → STOP). Press-and-hold sends the
   command at 5 Hz; release fires `STOP` automatically so the robot
   never coasts away on a thumb-slip.
3. **Status** — last reply from the Pi (e.g. `OK: UP`) is shown live
   so the user can verify commands are landing.

## Robot-side prerequisites

The Pi runs `scripts/ble_server.py` (a BLE → TCP bridge) which is what
this app talks to. The BLE service it advertises is the Nordic UART
Service, characteristics:

- `6E400002-…` — write, phone → robot (commands)
- `6E400003-…` — notify, robot → phone (replies)

See the root `README.md` for the BLE server install / autostart.

## Build + ship

The deploy mirrors AislePrompt's flow exactly.

```bash
# One-time on a fresh machine
npm install

# Build + auto-submit to TestFlight
bash scripts/build-and-submit.sh

# Or split:
bash scripts/build-and-submit.sh build    # queue an EAS build, exit
bash scripts/build-and-submit.sh submit   # wait for last build, submit it
```

The script reuses the same App Store Connect API key as AislePrompt
(`/home/voidsstr/.aisleprompt/AuthKey_WLK228JB3P.p8`) because that key
is scoped to the developer team, not to a specific app — so it works
for any app you ship under team `ZYH6M3S4ZF`.

### What you (the developer) need to do once on the Apple side

EAS handles the auto-provisioning for the App ID and certificates on
the first build. The one thing Apple still requires manual setup for
is the **App Store Connect record** for `com.aisleprompt.robotcontrol`:

1. Go to https://appstoreconnect.apple.com/apps
2. Click **+** → **New App**
3. Platforms: iOS, Name: **Robot Control**, Primary language: English (U.S.)
4. Bundle ID: pick `com.aisleprompt.robotcontrol` from the dropdown
   (it'll appear there after the first EAS build registers the App ID
   in the developer portal). SKU: `robot-control-001`.
5. Once created, copy the **App Store Connect App ID** (the long
   numeric one in the URL, e.g. `6789012345`) and add it to
   `eas.json` under `submit.production.ios.ascAppId`. Without this
   the `submit` step has to ask which ASC record to attach to on
   first run.

After that, `bash scripts/build-and-submit.sh` runs end-to-end without
intervention.

## Source-tree

```
ios-app/
  App.tsx                       # 2-screen router (Scan ↔ Control)
  index.ts                      # Expo entry point
  app.json                      # bundle id, BLE permission strings, plugin
  eas.json                      # build + submit profiles
  package.json
  scripts/
    build-and-submit.sh         # EAS build → watch → TestFlight submit
  src/
    lib/
      ble.ts                    # NUS scan / connect / send / notify wrapper
    screens/
      ScanScreen.tsx            # pairing list + auto-reconnect on launch
      ControlScreen.tsx         # 5-button tank pad
```
