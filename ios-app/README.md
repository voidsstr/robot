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

The deploy mirrors AislePrompt's flow exactly — once credentials are
bootstrapped, every subsequent build is fully non-interactive.

```bash
# One-time on a fresh machine
npm install

# ── ONE-TIME interactive step (only on a new bundle id) ──────────────
# EAS asks Apple for a Distribution Cert + Provisioning Profile for
# com.aisleprompt.robotcontrol. Needs your Apple ID + app-specific pwd.
bash scripts/bootstrap-credentials.sh

# ── Build + auto-submit to TestFlight (idempotent, non-interactive) ──
bash scripts/build-and-submit.sh

# Or split:
bash scripts/build-and-submit.sh build    # queue an EAS build, exit
bash scripts/build-and-submit.sh submit   # wait for last build, submit it
```

The submit step reuses the same App Store Connect API key as
AislePrompt (`/home/voidsstr/.aisleprompt/AuthKey_WLK228JB3P.p8`)
because that key is scoped to the developer team, not to a specific
app — so it works for any app you ship under team `ZYH6M3S4ZF`. The
ASC App ID for this app is **6768445191** and is already wired into
`eas.json`.

### Why the one-time bootstrap is needed

Apple exposes two separate APIs:

| Operation                              | Auth required by Apple |
|----------------------------------------|------------------------|
| Submit a build, manage TestFlight      | ASC API key (.p8) ✓     |
| Generate a Distribution Certificate    | Apple ID + password    |
| Register a new App ID / Profile        | Apple ID + password    |

The ASC API key (which AislePrompt's deploy uses) covers submits but
not Developer Portal credential creation. So a fresh bundle id needs
one interactive run of `eas credentials` to mint the cert + profile.
After that, the cached credentials carry every subsequent build,
which is why `build-and-submit.sh` runs head-less from then on.

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
