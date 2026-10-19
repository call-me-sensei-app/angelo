#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# An isolated editor-validation build, NOT the full release feature set.
# Does not fetch dependencies, enable accounts, or replace installed Blender.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
# ANGELO_* wins; the pre-rename SENSEI_* and TOONLAB_* spellings remain deprecated
# fallbacks (the rule of angelo_agent/product_env.py).
BUILD="${ANGELO_NATIVE_BUILD_DIR:-${SENSEI_NATIVE_BUILD_DIR:-${TOONLAB_NATIVE_BUILD_DIR:-$SOURCE/../build-angelo-native}}}"
JOBS="${ANGELO_BUILD_JOBS:-${SENSEI_BUILD_JOBS:-${TOONLAB_BUILD_JOBS:-10}}}"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This validated preset is for macOS arm64; use Blender's platform build instructions elsewhere." >&2
  exit 1
fi
if [[ ! -e "$SOURCE/lib/macos_arm64/.git" || ! -x "$SOURCE/lib/macos_arm64/python/bin/python3.13" ]]; then
  echo "Fetch the pinned macOS libraries first; see doc/angelo/native_editor_build.rst." >&2
  exit 1
fi
# Optional: bundle the agent runtime (the patched Codex build) from ANGELO_AGENT_RUNTIME_DIR; without it the app
# uses the person's own Codex, or the build named by ANGELO_AGENT_RUNTIME at run time.
AGENT_RUNTIME="${ANGELO_AGENT_RUNTIME_DIR:-}"
if [[ -n "$AGENT_RUNTIME" ]]; then
  python3 "$SOURCE/scripts/angelo/release_checks.py" agent-runtime "$AGENT_RUNTIME" --platform macos
  AGENT_RUNTIME="$(cd "$AGENT_RUNTIME" && pwd)"  # CMake records an absolute path.
fi
cmake -S "$SOURCE" -B "$BUILD" -G Ninja \
  -C "$SOURCE/build_files/cmake/config/blender_lite.cmake" \
  -DANGELO_AGENT_RUNTIME_DIR="$AGENT_RUNTIME" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  -DWITH_IMAGE_WEBP=ON -DWITH_BUILDINFO=ON -DWITH_ASSERT_ABORT=OFF -DWITH_OPENSUBDIV=ON
cmake --build "$BUILD" --target install --parallel "$JOBS"
# Optional stable development signature (ANGELO_DEV_SIGN_ID); a no-op when unset.
"$SOURCE/scripts/angelo/dev_codesign.sh" "$BUILD/bin/Angelo.app"
echo "Native development app: $BUILD/bin/Angelo.app"
