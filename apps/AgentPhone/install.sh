#!/bin/bash
# Build Agent Phone (Release) and install it as a normal app, so it shows up in
# Launchpad and can be kept in the Dock.
set -euo pipefail
cd "$(dirname "$0")"

command -v xcodegen >/dev/null || brew install xcodegen
xcodegen generate >/dev/null
xcodebuild -project AgentPhone.xcodeproj -scheme AgentPhone -configuration Release \
  -derivedDataPath .build/release build | tail -1

APP=".build/release/Build/Products/Release/AgentPhone.app"
DEST_DIR="/Applications"
[ -w "$DEST_DIR" ] || { DEST_DIR="$HOME/Applications"; mkdir -p "$DEST_DIR"; }
DEST="$DEST_DIR/Agent Phone.app"

ditto "$APP" "$DEST"
echo "Installed $DEST"
open "$DEST"
