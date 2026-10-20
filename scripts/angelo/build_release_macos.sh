#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# The full-feature macOS arm64 build: Blender's `blender_release.cmake` (Cycles with
# Metal, OpenVDB, USD, Alembic, FFmpeg, audio, i18n, the Quick Look thumbnailer and
# the rest of the release feature set) with Angelo's bundle, icons and splash.
# `build_native.sh` stays the fast editor-validation build.
#
# Never fetches dependencies or Git LFS objects, never signs beyond the ad-hoc
# linker signature (plus the optional ANGELO_DEV_SIGN_ID development identity), and
# never touches an installed Blender. See doc/angelo/native_editor_build.rst.
#
#   ANGELO_AGENT_RUNTIME_DIR  required: the folder with the agent runtime (the patched Codex build:
#                             codex, codex-code-mode-host), installed into Angelo.app/Contents/Helpers
#   ANGELO_RELEASE_BUILD_DIR  output directory (default: ../build-angelo-release)
#   ANGELO_LIBDIR             pre-compiled libraries (default: see below)
#   ANGELO_BUILD_JOBS         parallel jobs (default: 10)
#   ANGELO_RELEASE_CONFIGURE_ONLY=1   configure, then stop before compiling
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="${ANGELO_RELEASE_BUILD_DIR:-$SOURCE/../build-angelo-release}"
JOBS="${ANGELO_BUILD_JOBS:-10}"
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This preset is for macOS arm64; use Blender's platform build instructions elsewhere." >&2
  exit 1
fi
# Nothing below may smudge LFS content or reach the network.
export GIT_LFS_SKIP_SMUDGE=1

# Pre-compiled libraries: the explicit override, this checkout's own submodule, or
# (for a linked worktree, where the multi-gigabyte submodule is not populated) the
# main checkout's, used read-only.
is_lib_dir() { [[ -e "$1/.git" && -x "$1/python/bin/python3.13" ]]; }
LIBDIR="${ANGELO_LIBDIR:-}"
if [[ -z "$LIBDIR" ]]; then
  if is_lib_dir "$SOURCE/lib/macos_arm64"; then
    LIBDIR="$SOURCE/lib/macos_arm64"
  else
    MAIN="$(git -C "$SOURCE" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
    if [[ -n "$MAIN" ]] && is_lib_dir "$MAIN/lib/macos_arm64"; then
      LIBDIR="$MAIN/lib/macos_arm64"
    fi
  fi
fi
if [[ -z "$LIBDIR" ]] || ! is_lib_dir "$LIBDIR"; then
  echo "Fetch the pinned macOS libraries first, or set ANGELO_LIBDIR; see doc/angelo/native_editor_build.rst." >&2
  exit 1
fi
PINNED="$(git -C "$SOURCE" ls-tree HEAD lib/macos_arm64 2>/dev/null | awk '{print $3}')"
ACTUAL="$(git -C "$LIBDIR" rev-parse HEAD 2>/dev/null || true)"
if [[ -n "$PINNED" && -n "$ACTUAL" && "$PINNED" != "$ACTUAL" ]]; then
  echo "warning: $LIBDIR is at ${ACTUAL:0:12}, this source pins ${PINNED:0:12}" >&2
fi
# A library that is still a Git LFS pointer would fail late, deep inside the link.
for probe in openvdb/lib/libopenvdb.dylib usd/lib/libusd_ms.dylib ffmpeg/lib/libavcodec.a; do
  if [[ ! -s "$LIBDIR/$probe" ]] || [[ "$(head -c 7 "$LIBDIR/$probe")" == "version" ]]; then
    echo "$LIBDIR/$probe is missing or an LFS pointer; the libraries are not fully fetched." >&2
    exit 1
  fi
done
# Source assets (icons, fonts, startup files, splash) are LFS-tracked in the source tree.
for probe in release/datafiles/startup.blend release/datafiles/splash.png release/datafiles/icons_blend/ui.blend; do
  if [[ -e "$SOURCE/$probe" ]] && [[ "$(head -c 7 "$SOURCE/$probe")" == "version" ]]; then
    echo "$SOURCE/$probe is an LFS pointer; run 'git lfs checkout' (local objects only) first." >&2
    exit 1
  fi
done
# Blender's essential brushes and node groups are installed from this separate
# tree. A checkout can have the startup/splash materialized but still contain
# pointers here; that used to produce a successful build with missing features.
if [[ ! -d "$SOURCE/assets" ]]; then
  echo "$SOURCE/assets is missing; restore the tracked essential assets first." >&2
  exit 1
fi
while IFS= read -r -d '' asset; do
  if [[ "$(head -c 7 "$asset")" == "version" ]]; then
    echo "$asset is an LFS pointer; run 'git lfs checkout assets' (local objects only) first." >&2
    exit 1
  fi
done < <(find "$SOURCE/assets" -type f -name '*.blend' -print0)
echo "Libraries: $LIBDIR"
# The agent runtime every release bundles (scripts/addons_core/angelo_agent/agent_runtime.json): its files, the
# pinned version and the cache-affinity parameter, asked of the binaries themselves (offline) before compiling.
AGENT_RUNTIME="${ANGELO_AGENT_RUNTIME_DIR:-}"
if [[ -z "$AGENT_RUNTIME" ]]; then
  echo "Set ANGELO_AGENT_RUNTIME_DIR to the folder with the agent runtime (codex, codex-code-mode-host);" \
    "a release bundles it. See doc/angelo/releasing.rst, \"Agent runtime\"." >&2
  exit 1
fi
# The source is exactly its commit: an untracked file would be installed by the folder installs (shipped_sources.py).
python3 "$SOURCE/scripts/angelo/release_checks.py" source-clean "$SOURCE"
# A release ships its own notes dated: What's New draws an undated entry as "Unreleased" (release_dated.py).
python3 "$SOURCE/scripts/angelo/release_checks.py" release-dated "$SOURCE"
python3 "$SOURCE/scripts/angelo/release_checks.py" agent-runtime "$AGENT_RUNTIME" --platform macos
AGENT_RUNTIME="$(cd "$AGENT_RUNTIME" && pwd)"  # CMake records an absolute path.

cmake -S "$SOURCE" -B "$BUILD" -G Ninja \
  -C "$SOURCE/build_files/cmake/config/blender_release.cmake" \
  -DLIBDIR="$LIBDIR" \
  -DANGELO_AGENT_RUNTIME_DIR="$AGENT_RUNTIME" -DANGELO_REQUIRE_AGENT_RUNTIME=ON \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
if [[ "${ANGELO_RELEASE_CONFIGURE_ONLY:-}" == "1" ]]; then
  echo "Configured: $BUILD"
  exit 0
fi
cmake --build "$BUILD" --target install --parallel "$JOBS"
# A file Git LFS never checked out would ship as a text stub: refuse the bundle (release_checks.lfs_pointer_files).
python3 "$SOURCE/scripts/angelo/release_checks.py" lfs-pointers "$BUILD/bin/Angelo.app"
python3 "$SOURCE/scripts/angelo/release_checks.py" tracked-scripts "$BUILD/bin/Angelo.app/Contents/Resources/5.2/scripts" \
  --source "$SOURCE" --libdir "$LIBDIR"
# No builder path or credential may ship: every file of the bundle, binaries included (release_checks.py
# builder-paths; doc/angelo/releasing.rst, "No builder paths"). The Windows build and publishing run it too.
python3 "$SOURCE/scripts/angelo/release_checks.py" builder-paths "$BUILD/bin/Angelo.app" \
  --identity-path "$SOURCE" --identity-path "$BUILD" --identity-path "$LIBDIR" \
  --report "$BUILD/angelo-builder-paths.json"
# Optional stable development signature (ANGELO_DEV_SIGN_ID); a no-op when unset.
"$SOURCE/scripts/angelo/dev_codesign.sh" "$BUILD/bin/Angelo.app"
echo "Native release app: $BUILD/bin/Angelo.app"
