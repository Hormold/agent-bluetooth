#!/bin/bash
# Agent Phone quality gate: project generation, lint, format, and a warnings-as-errors
# build (Debug and Release). Headless; the same script runs in CI (.github/workflows/app.yml).
#
#   apps/AgentPhone/check.sh            # everything
#   apps/AgentPhone/check.sh --no-build # lint and format only (seconds)
set -euo pipefail
cd "$(dirname "$0")"

need() { command -v "$1" >/dev/null || { echo "missing $1: $2" >&2; exit 1; }; }
need xcodegen "brew install xcodegen"
need swiftlint "brew install swiftlint"
xcrun --find swift-format >/dev/null || { echo "missing swift-format: install Xcode 16+" >&2; exit 1; }

echo "== xcodegen"
xcodegen generate --quiet

echo "== swiftlint --strict"
swiftlint lint --strict --quiet

echo "== swift-format lint --strict"
xcrun swift-format lint --strict --recursive --parallel Sources

[ "${1:-}" = "--no-build" ] && { echo "OK (build skipped)"; exit 0; }

# CODE_SIGNING_ALLOWED=NO: CI has no certificates; signing is release.sh's job.
for config in Debug Release; do
    echo "== xcodebuild $config"
    xcodebuild -project AgentPhone.xcodeproj -scheme AgentPhone -configuration "$config" \
        -destination 'generic/platform=macOS' -derivedDataPath .build/check -skipPackagePluginValidation -skipMacroValidation \
        CODE_SIGNING_ALLOWED=NO build -quiet
done
echo "OK"
