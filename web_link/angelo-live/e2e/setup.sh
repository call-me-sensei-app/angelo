#!/bin/sh
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
# Pack the package as npm would publish it, and install it (with each app's own dependencies) into every e2e app.
set -e
cd "$(dirname "$0")/.."
PACKED="$(pwd)/e2e/.packed"   # The packed tarball only, whatever npm names it.
rm -rf "$PACKED"
mkdir -p "$PACKED"
npm pack --silent --pack-destination "$PACKED" >/dev/null
TARBALL="$(ls "$PACKED"/*.tgz)"
for app in e2e/apps/*/; do
  [ -f "$app/package.json" ] || continue
  (cd "$app" && npm install --no-audit --no-fund --silent && npm install --no-audit --no-fund --silent --no-save "$TARBALL")
done
echo "installed $TARBALL"
