#!/usr/bin/env bash
# Robot Control — production build + TestFlight submit.
#
# Mirrors the aisleprompt deploy flow exactly:
#   1. eas build --profile production --platform ios --non-interactive --no-wait
#   2. Watch the build, auto-submit on FINISHED to TestFlight
#
# Reuses the App Store Connect API key under ~/.aisleprompt/ since the
# Robot Control app lives in the same Apple Developer team (ZYH6M3S4ZF)
# and the .p8 key is team-scoped, not app-scoped.
#
# OTA (expo-updates) flow:
#   This binary build is what TestFlight installs. Once a tester is on the
#   new build, JS-only changes can be shipped instantly via:
#     npm run ota:production    # eas update --branch production --auto
#   Native changes (new modules, plugin changes, version bump) still
#   require a fresh binary via this script. The runtimeVersion policy
#   in app.json is "appVersion", so OTA is only delivered to builds with
#   the same expo.version — bump expo.version when shipping native deltas.
#
# Usage:
#   bash scripts/build-and-submit.sh        # build + auto-submit
#   bash scripts/build-and-submit.sh build  # build only
#   bash scripts/build-and-submit.sh submit # submit latest build only

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# Silence the noisy Expo-Go production warning — we ship a custom
# dev client + native plugins so Expo Go isn't part of the pipeline.
export EAS_BUILD_NO_EXPO_GO_WARNING=true

# Re-use the AislePrompt ASC API key for submit auth (the .p8 is team-
# scoped, so it works for every app under team ZYH6M3S4ZF).
if [ -f "$HOME/.aisleprompt/.env" ]; then
  # shellcheck disable=SC1090
  . "$HOME/.aisleprompt/.env"
fi
export EXPO_ASC_API_KEY_PATH="${EXPO_ASC_API_KEY_PATH:-$HOME/.aisleprompt/AuthKey_WLK228JB3P.p8}"
export EXPO_ASC_API_KEY_ID="${EXPO_ASC_API_KEY_ID:-WLK228JB3P}"
export EXPO_ASC_API_KEY_ISSUER_ID="${EXPO_ASC_API_KEY_ISSUER_ID:-40d3c8d7-c8b3-4691-b0a2-e03946796093}"

MODE="${1:-all}"
LOG="/tmp/robot-control-eas-build.log"

if [ "$MODE" = "all" ] || [ "$MODE" = "build" ]; then
  echo "[$(date '+%H:%M:%S')] Starting EAS build (ios/production, non-interactive)…" | tee -a "$LOG"
  RAW=$(npx --yes eas-cli@latest build \
    --platform ios --profile production \
    --non-interactive --no-wait --json)
  echo "$RAW" >>"$LOG"
  BUILD_ID=$(echo "$RAW" | python3 -c "import sys,json; d=json.load(sys.stdin); items=d if isinstance(d,list) else [d]; print(items[0].get('id',''))")
  if [ -z "$BUILD_ID" ]; then
    echo "Could not extract build id from EAS response. See $LOG."
    exit 1
  fi
  echo "[$(date '+%H:%M:%S')] Build queued: $BUILD_ID" | tee -a "$LOG"
  echo "$BUILD_ID" > /tmp/robot-control-last-build-id

  if [ "$MODE" = "build" ]; then exit 0; fi
fi

if [ "$MODE" = "all" ] || [ "$MODE" = "submit" ]; then
  BUILD_ID="${BUILD_ID:-$(cat /tmp/robot-control-last-build-id 2>/dev/null || true)}"
  if [ -z "$BUILD_ID" ]; then
    echo "No build id to submit. Run with 'build' first or pass it in."
    exit 1
  fi
  echo "[$(date '+%H:%M:%S')] Watching $BUILD_ID until FINISHED…" | tee -a "$LOG"
  while true; do
    STATUS=$(npx --yes eas-cli@latest build:view "$BUILD_ID" --json 2>/dev/null \
      | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','UNKNOWN'))" 2>/dev/null)
    TS=$(date '+%H:%M:%S')
    echo "[$TS] status: $STATUS" | tee -a "$LOG"
    case "$STATUS" in
      FINISHED)
        echo "[$TS] Build complete — submitting to TestFlight" | tee -a "$LOG"
        npx --yes eas-cli@latest submit --platform ios --id "$BUILD_ID" --non-interactive | tee -a "$LOG"
        echo "[$(date '+%H:%M:%S')] submit done" | tee -a "$LOG"
        exit 0
        ;;
      ERRORED|CANCELED)
        echo "[$TS] Build $STATUS — not submitting" | tee -a "$LOG"
        exit 1
        ;;
    esac
    sleep 60
  done
fi
