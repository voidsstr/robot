#!/usr/bin/env bash
# One-time interactive bootstrap for iOS credentials.
#
# A brand-new bundle id (`com.aisleprompt.robotcontrol`) has no
# Distribution Certificate or Provisioning Profile in Apple's Developer
# Portal yet. EAS Build can auto-generate both, but it has to log in
# to Apple as a real user the first time because Apple doesn't expose
# those Developer Portal operations through the App Store Connect API
# key alone.
#
# Run this once on your laptop. It opens an interactive Apple-ID
# prompt; have your app-specific password ready
# (https://appleid.apple.com/account/manage → App-Specific Passwords).
#
# After this completes, the EAS server caches the Distribution
# Certificate + Profile against the team — subsequent runs of
# scripts/build-and-submit.sh work fully non-interactively.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

cat <<'NOTE'
══════════════════════════════════════════════════════════════════
  iOS credentials bootstrap — one-time interactive setup
──────────────────────────────────────────────────────────────────
  1. EAS will ask for your Apple ID + app-specific password.
  2. When prompted, choose:
       Platform        → iOS
       Profile         → production
       Action          → "Set up a new Distribution Certificate
                          and Provisioning Profile" (auto)
  3. EAS contacts Apple, generates the cert + profile, caches them
     in the Expo project. This step won't be needed again.
══════════════════════════════════════════════════════════════════
NOTE

# `eas credentials` is the only interactive entry point. Once it
# returns successfully, the next `eas build` is fully non-interactive.
npx --yes eas-cli@latest credentials --platform ios

cat <<'DONE'

✔ Credentials bootstrapped. You can now run:

    bash scripts/build-and-submit.sh

…to build + auto-submit to TestFlight non-interactively, the same
way AislePrompt's deploys work.
DONE
