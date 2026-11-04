#!/bin/bash
# Build, sign (Developer ID), notarize and publish Agent Phone to GitHub Releases,
# so people can download it instead of building it.
#
#   TEAM_ID=ABCDE12345 NOTARY_PROFILE=agentphone apps/AgentPhone/release.sh 0.1.0
#
# One-time setup:
#   - a "Developer ID Application" certificate in your login keychain
#   - notary credentials saved in the keychain, from an App Store Connect API key:
#       xcrun notarytool store-credentials agentphone \
#         --key AuthKey_XXXX.p8 --key-id XXXX --issuer <issuer uuid>
#     (or --apple-id you@example.com --team-id ABCDE12345 --password <app-specific password>)
#   - gh authenticated for the repo
set -euo pipefail

VERSION=${1:?usage: release.sh <version>}
: "${TEAM_ID:?set TEAM_ID to your Apple Developer team id}"
: "${NOTARY_PROFILE:?set NOTARY_PROFILE (see xcrun notarytool store-credentials)}"
cd "$(dirname "$0")"
git diff --quiet HEAD -- . ../../src || { echo "commit the app and daemon first: the release tags HEAD" >&2; exit 1; }

DIST=".build/dist"
OUT="$DIST/Agent-Phone-$VERSION"
APP_NAME="Agent Phone.app"

command -v xcodegen >/dev/null || brew install xcodegen
xcodegen generate >/dev/null

echo "Building $VERSION (Release, Developer ID)..."
xcodebuild -project AgentPhone.xcodeproj -scheme AgentPhone -configuration Release \
  -derivedDataPath "$DIST" \
  CODE_SIGN_STYLE=Manual \
  CODE_SIGN_IDENTITY="Developer ID Application" \
  DEVELOPMENT_TEAM="$TEAM_ID" \
  CODE_SIGN_INJECT_BASE_ENTITLEMENTS=NO \
  OTHER_CODE_SIGN_FLAGS="--timestamp" \
  MARKETING_VERSION="$VERSION" \
  CURRENT_PROJECT_VERSION="$(git rev-list --count HEAD)" \
  build | tail -1

mkdir -p "$OUT"
ditto "$DIST/Build/Products/Release/AgentPhone.app" "$OUT/$APP_NAME"

echo "Notarizing..."
ditto -c -k --keepParent "$OUT/$APP_NAME" "$OUT.zip"
xcrun notarytool submit "$OUT.zip" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$OUT/$APP_NAME"
ditto -c -k --keepParent "$OUT/$APP_NAME" "$OUT.zip"   # zip again, now with the ticket stapled
spctl --assess --type execute --verbose "$OUT/$APP_NAME"

echo "Publishing v$VERSION..."
# Notes: this version's CHANGELOG.md section, then how to install the app.
NOTES="$DIST/notes-$VERSION.md"
awk -v v="$VERSION" '$0 ~ "^## "v" " {on=1; next} /^## / {on=0} on' ../../CHANGELOG.md > "$NOTES"
[ -s "$NOTES" ] || { echo "CHANGELOG.md has no section for $VERSION" >&2; exit 1; }
cat >> "$NOTES" <<'TXT'

### Install

Agent Phone (macOS): download the zip below, move Agent Phone to Applications and open it. It checks what's missing (the Agent Bluetooth service, LiveKit, permissions) and sets it up.

Daemon and CLI: `uv tool install --force git+https://github.com/Hormold/agent-bluetooth@vVERSION`
TXT
sed -i '' "s/vVERSION/v$VERSION/" "$NOTES"
gh release create "v$VERSION" "$OUT.zip" \
  --repo Hormold/agent-bluetooth \
  --target "$(git rev-parse HEAD)" \
  --title "Agent Bluetooth $VERSION" \
  --notes-file "$NOTES"

echo "Done: https://github.com/Hormold/agent-bluetooth/releases/tag/v$VERSION"
