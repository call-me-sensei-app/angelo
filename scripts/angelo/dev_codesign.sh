#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# Optional local-development signing so the macOS Keychain recognizes rebuilds
# of the development app as the same application (saved API keys; see
# doc/angelo/ai_providers.rst, "Saved API keys"). A no-op unless
# ANGELO_DEV_SIGN_ID (deprecated fallbacks: SENSEI_DEV_SIGN_ID, TOONLAB_DEV_SIGN_ID) names a
# code-signing identity already in your keychain.
#
# Use an "Apple Development" identity (Xcode > Settings > Accounts > Manage
# Certificates). securityd gives Apple-issued development and Developer ID
# signatures a team partition; any other signature, including a self-signed
# certificate, is identified per build by its cdhash, so macOS would still ask
# after every rebuild. Release signing and notarization are a separate pipeline.
#
# Only the main executable is re-signed, as the linker's ad-hoc signature was:
# no resource seal, so bundled scripts can still change between builds. No
# hardened runtime is added, so debuggers can still attach.
#
# Usage: scripts/angelo/dev_codesign.sh <path/to/App.app>
set -euo pipefail
APP="${1:?usage: dev_codesign.sh <path/to/App.app>}"
# ANGELO_* wins; the pre-rename SENSEI_* and TOONLAB_* spellings remain deprecated fallbacks.
SIGN_ID="${ANGELO_DEV_SIGN_ID:-${SENSEI_DEV_SIGN_ID:-${TOONLAB_DEV_SIGN_ID:-}}}"
if [[ -z "$SIGN_ID" ]]; then
  exit 0
fi
if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "ANGELO_DEV_SIGN_ID applies only to macOS builds." >&2
  exit 1
fi
PLIST="$APP/Contents/Info.plist"
if [[ ! -f "$PLIST" ]]; then
  echo "dev_codesign: no app bundle at $APP" >&2
  exit 1
fi
EXECUTABLE="$APP/Contents/MacOS/$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$PLIST")"
IDENTIFIER="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$PLIST")"
if [[ ! -x "$EXECUTABLE" ]]; then
  echo "dev_codesign: no executable at $EXECUTABLE" >&2
  exit 1
fi
if ! codesign --force --sign "$SIGN_ID" --identifier "$IDENTIFIER" "$EXECUTABLE"; then
  echo "dev_codesign: signing with \"$SIGN_ID\" failed. The app is built but still ad-hoc" >&2
  echo "signed, so saved API keys will ask for Keychain approval after each rebuild." >&2
  exit 1
fi
codesign --verify --strict "$EXECUTABLE"
echo "Signed $EXECUTABLE for local development."
codesign --display --requirements - "$EXECUTABLE" 2>&1 | sed -n 's/^#* *designated => /Designated requirement: /p'
