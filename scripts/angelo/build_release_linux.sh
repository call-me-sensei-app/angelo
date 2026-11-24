#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# The full-feature Linux x86-64 release build, in Blender's own Linux release environment: Rocky Linux 8 (glibc 2.28)
# with gcc-toolset-14 (build_files/build_environment/linux/linux_rocky8_setup.sh), so the app runs on any distribution
# with glibc 2.28 or newer. `build_release_linux_container.sh` runs this inside a rockylinux:8 container on any
# Linux host. doc/angelo/releasing.rst, "Linux package".
#
# Never fetches dependencies or Git LFS objects and never signs. The output is the portable install
# ($BUILD/install), its split debug symbols ($BUILD/debug-symbols, never shipped) and $BUILD/angelo-release-build.json;
# `linux_package.py stage|pack|check` makes the tar.xz from it.
#
#   ANGELO_AGENT_RUNTIME_DIR   required: the Linux agent runtime (agent_runtime.json, platform linux)
#   ANGELO_GPU_TOOLKITS        the folder with the GPU SDKs at Blender's pins, laid out as Blender's buildbot expects
#                              (cuda-<major.minor>, optix-sdk-<v>, rocm-<v>, dlss-sdk-<v>): the release build uses
#                              upstream's buildbot config (build_files/buildbot/config/blender_linux.cmake)
#   ANGELO_ALLOW_MISSING_GPU=1 a build without the GPU kernels (development, CI, rehearsal): recorded, never a release
#   ANGELO_ALLOW_MISSING_RUNTIME=1  a build without the agent runtime (likewise recorded, never a release)
#   ANGELO_RELEASE_BUILD_DIR   output directory (default: ../build-angelo-linux-release)
#   ANGELO_LIBDIR              pre-compiled libraries (default: lib/linux_x64 of this or the main checkout)
#   ANGELO_BUILD_JOBS          parallel jobs (default: the CPU count)
#   ANGELO_LINUX_ANY_GLIBC=1   build on a host whose glibc is not 2.28 (development only: the package check refuses
#                              the result)
#   ANGELO_RELEASE_CONFIGURE_ONLY=1   configure, then stop before compiling
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="${ANGELO_RELEASE_BUILD_DIR:-$SOURCE/../build-angelo-linux-release}"
JOBS="${ANGELO_BUILD_JOBS:-$(nproc)}"
fail() { echo "build_release_linux: $*" >&2; exit 1; }
[[ "$(uname -s)" == "Linux" && "$(uname -m)" == "x86_64" ]] || fail "this build is for Linux x86-64"
export GIT_LFS_SKIP_SMUDGE=1   # nothing below may smudge LFS content or reach the network

# Blender's release environment: glibc 2.28 (the floor the downloads page states) and GCC 14 (gcc-toolset-14).
GLIBC="$(getconf GNU_LIBC_VERSION | awk '{print $2}')"
if [[ "$GLIBC" != "2.28" && "${ANGELO_LINUX_ANY_GLIBC:-}" != "1" ]]; then
  fail "glibc $GLIBC: build in Blender's Rocky Linux 8 environment (glibc 2.28; build_release_linux_container.sh)"
fi
GCC_MAJOR="$(gcc -dumpversion | cut -d. -f1)"
if [[ "$GCC_MAJOR" != "14" && "${ANGELO_LINUX_ANY_GLIBC:-}" != "1" ]]; then
  fail "gcc $GCC_MAJOR: enable gcc-toolset-14 first (source /opt/rh/gcc-toolset-14/enable)"
fi

is_lib_dir() { [[ -x "$1/python/bin/python3.13" ]]; }
LIBDIR="${ANGELO_LIBDIR:-}"
if [[ -z "$LIBDIR" ]]; then
  if is_lib_dir "$SOURCE/lib/linux_x64"; then
    LIBDIR="$SOURCE/lib/linux_x64"
  else
    MAIN="$(git -C "$SOURCE" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')"
    if [[ -n "$MAIN" ]] && is_lib_dir "$MAIN/lib/linux_x64"; then LIBDIR="$MAIN/lib/linux_x64"; fi
  fi
fi
[[ -n "$LIBDIR" ]] && is_lib_dir "$LIBDIR" || fail "fetch the pinned lib/linux_x64 first, or set ANGELO_LIBDIR"
PINNED="$(git -C "$SOURCE" ls-tree HEAD lib/linux_x64 2>/dev/null | awk '{print $3}')"
ACTUAL="$(git -C "$LIBDIR" rev-parse HEAD 2>/dev/null || true)"
if [[ -n "$PINNED" && -n "$ACTUAL" && "$PINNED" != "$ACTUAL" ]]; then
  fail "$LIBDIR is at ${ACTUAL:0:12}, this source pins ${PINNED:0:12}"
fi
# A release ships upstream's libraries exactly as pinned (and the builder-paths scan excuses upstream's own build
# paths only in files byte-identical to that tree): the library checkout must be a clean git checkout at the pin.
if [[ -z "${ANGELO_ALLOW_MISSING_GPU:-}${ANGELO_ALLOW_MISSING_RUNTIME:-}" ]]; then
  [[ -n "$ACTUAL" ]] || fail "$LIBDIR is not a git checkout: a release needs lib/linux_x64 as a clean checkout at the pin"
  [[ -z "$(git -C "$LIBDIR" status --porcelain --untracked-files=no 2>/dev/null)" ]] \
    || fail "$LIBDIR has local changes: a release needs lib/linux_x64 exactly as pinned"
fi
for probe in openvdb/lib/libopenvdb.so usd/lib/libusd_ms.so python/bin/python3.13; do
  if [[ ! -s "$LIBDIR/$probe" ]] || [[ "$(head -c 7 "$LIBDIR/$probe")" == "version" ]]; then
    fail "$LIBDIR/$probe is missing or an LFS pointer; the libraries are not fully fetched"
  fi
done
for probe in release/datafiles/startup.blend release/datafiles/splash.png release/datafiles/icons_blend/ui.blend; do
  if [[ -e "$SOURCE/$probe" ]] && [[ "$(head -c 7 "$SOURCE/$probe")" == "version" ]]; then
    fail "$SOURCE/$probe is an LFS pointer; run 'git lfs checkout' (local objects only) first"
  fi
done
echo "Libraries: $LIBDIR"
# The pinned libraries' Python runs every check (Rocky Linux 8's own python3 is 3.6).
PY="$LIBDIR/python/bin/python3.13"

"$PY" "$SOURCE/scripts/angelo/release_checks.py" source-clean "$SOURCE"
"$PY" "$SOURCE/scripts/angelo/release_checks.py" release-dated "$SOURCE"
# The agent runtime every release bundles, or none (ANGELO_ALLOW_MISSING_RUNTIME=1: recorded, never a release).
AGENT_RUNTIME="${ANGELO_AGENT_RUNTIME_DIR:-}"
RUNTIME_ARGS=()
RUNTIME_FOLDER=""
MISSING_RUNTIME=0
if [[ -n "$AGENT_RUNTIME" ]]; then
  "$PY" "$SOURCE/scripts/angelo/release_checks.py" agent-runtime "$AGENT_RUNTIME" --platform linux
  AGENT_RUNTIME="$(cd "$AGENT_RUNTIME" && pwd)"
  RUNTIME_ARGS=(-DANGELO_AGENT_RUNTIME_DIR="$AGENT_RUNTIME" -DANGELO_REQUIRE_AGENT_RUNTIME=ON)
  # Where the install puts it (the pin's linux folder; the agent-runtime check above already refused a pin without
  # one): the symbol split must leave it byte for byte.
  RUNTIME_FOLDER="$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["platforms"]["linux"]["folder"])' \
    "$SOURCE/scripts/addons_core/angelo_agent/agent_runtime.json")" || fail "agent_runtime.json names no linux folder"
elif [[ "${ANGELO_ALLOW_MISSING_RUNTIME:-}" == "1" ]]; then
  MISSING_RUNTIME=1
  RUNTIME_ARGS=(-DANGELO_AGENT_RUNTIME_DIR= -DANGELO_REQUIRE_AGENT_RUNTIME=OFF)
else
  fail "set ANGELO_AGENT_RUNTIME_DIR to the Linux agent runtime folder (doc/angelo/releasing.rst, \"Agent runtime\")"
fi

# The GPU parts: upstream's buildbot config with the SDKs at Blender's pins, or none (recorded, never a release).
mkdir -p "$BUILD"
BUILD="$(cd "$BUILD" && pwd)"   # absolute, as CMake records it
TOOLKIT_ARGS=()   # before -C: the buildbot config asserts these folders
GPU_ARGS=()       # after -C: blender_release.cmake force-sets the GPU options on, these turn them off
ALLOWED_MISSING="[]"
if [[ -n "${ANGELO_GPU_TOOLKITS:-}" ]]; then
  TOOLKITS="$(cd "$ANGELO_GPU_TOOLKITS" && pwd)"
  "$PY" "$SOURCE/scripts/angelo/linux_gpu_toolkits.py" versions "$TOOLKITS" --source "$SOURCE" \
    > "$BUILD/gpu-versions.json" || fail "the GPU SDKs in $TOOLKITS are not Blender's pins (above)"
  CONFIG="$SOURCE/build_files/buildbot/config/blender_linux.cmake"
  TOOLKIT_ARGS=(-DBUILDBOT_CUDA_TOOLKITS_ROOT_DIR="$TOOLKITS" -DBUILDBOT_OPTIX_TOOLKITS_ROOT_DIR="$TOOLKITS"
                -DBUILDBOT_HIP_TOOLKITS_ROOT_DIR="$TOOLKITS" -DBUILDBOT_DLSS_TOOLKITS_ROOT_DIR="$TOOLKITS")
elif [[ "${ANGELO_ALLOW_MISSING_GPU:-}" == "1" ]]; then
  CONFIG="$SOURCE/build_files/cmake/config/blender_release.cmake"
  GPU_ARGS=(-DWITH_CYCLES_CUDA_BINARIES=OFF -DWITH_CYCLES_DEVICE_OPTIX=OFF -DWITH_CYCLES_HIP_BINARIES=OFF
            -DWITH_CYCLES_DEVICE_HIPRT=OFF -DWITH_CYCLES_ONEAPI_BINARIES=OFF -DWITH_CYCLES_DEVICE_ONEAPI=OFF
            -DWITH_DLSS=OFF -DWITH_DOC_MANPAGE=OFF)
  ALLOWED_MISSING='["cuda", "optix", "hip", "hiprt", "oneapi", "dlss"]'
  rm -f "$BUILD/gpu-versions.json"
else
  fail "set ANGELO_GPU_TOOLKITS (a release) or ANGELO_ALLOW_MISSING_GPU=1 (a build without GPU kernels)"
fi

if [[ "$MISSING_RUNTIME" == "1" ]]; then
  ALLOWED_MISSING="$("$PY" -c 'import json, sys; print(json.dumps(json.loads(sys.argv[1]) + ["agent-runtime"]))' "$ALLOWED_MISSING")"
fi
"$SOURCE/scripts/angelo/release_libdir_cache.sh" check "$BUILD" "$LIBDIR" "$ACTUAL"
# Unix Makefiles, upstream's default on Linux: Rocky Linux 8's ninja (1.8) cannot read this build graph's
# multiple-output rules.
# CMake reads its arguments in order (see TOOLKIT_ARGS and GPU_ARGS above).
cmake -S "$SOURCE" -B "$BUILD" -G "Unix Makefiles" "${TOOLKIT_ARGS[@]}" -C "$CONFIG" \
  -DLIBDIR="$LIBDIR" -DWITH_INSTALL_PORTABLE=ON -DCMAKE_INSTALL_PREFIX="$BUILD/install" \
  -DCMAKE_BUILD_TYPE=Release "${RUNTIME_ARGS[@]}" "${GPU_ARGS[@]}"
"$SOURCE/scripts/angelo/release_libdir_cache.sh" record "$BUILD" "$LIBDIR" "$ACTUAL" "$PINNED"
"$PY" "$SOURCE/scripts/angelo/linux_build_record.py" write "$BUILD" --source "$SOURCE" --libdir "$LIBDIR" \
  --allowed-missing "$ALLOWED_MISSING" --agent-runtime "${AGENT_RUNTIME:-none}"
if [[ "${ANGELO_RELEASE_CONFIGURE_ONLY:-}" == "1" ]]; then
  echo "Configured: $BUILD"
  exit 0
fi
START=$(date +%s)
cmake --build "$BUILD" --target install --parallel "$JOBS"
INSTALL="$BUILD/install"   # its own folder: $BUILD/bin also holds build tools (makesdna, makesrna, msgfmt)
# Debug symbols go to $BUILD/debug-symbols (kept with the release kit for crash reports, never shipped); the shipped
# files keep only a debuglink naming the symbol file.
"$PY" "$SOURCE/scripts/angelo/linux_build_record.py" strip "$BUILD" ${RUNTIME_FOLDER:+--runtime-folder "$RUNTIME_FOLDER"}
"$PY" "$SOURCE/scripts/angelo/linux_build_record.py" built "$BUILD" --seconds $(( $(date +%s) - START ))
"$PY" "$SOURCE/scripts/angelo/release_checks.py" lfs-pointers "$INSTALL"
SERIES="$(awk '$1 == "#define" && $2 == "BLENDER_VERSION" { printf "%d.%d", $3 / 100, $3 % 100 }' \
  "$SOURCE/source/blender/blenkernel/BKE_blender_version.h")"
"$PY" "$SOURCE/scripts/angelo/release_checks.py" tracked-scripts "$INSTALL/$SERIES/scripts" \
  --source "$SOURCE" --libdir "$LIBDIR"
# Inside the container HOME is /tmp and the user has no name: the host's own user and home come in from the wrapper
# (build_release_linux_container.sh), so a path of the real builder fails here too, never only at packaging.
HOST_IDENTITY=()
[[ -n "${ANGELO_HOST_USER:-}" ]] && HOST_IDENTITY+=(--identity "$ANGELO_HOST_USER")
[[ -n "${ANGELO_HOST_HOME:-}" ]] && HOST_IDENTITY+=(--identity-path "$ANGELO_HOST_HOME")
"$PY" "$SOURCE/scripts/angelo/release_checks.py" builder-paths "$INSTALL" \
  --identity-path "$SOURCE" --identity-path "$BUILD" --identity-path "$LIBDIR" "${HOST_IDENTITY[@]}" \
  --upstream-root "$LIBDIR" --report "$BUILD/angelo-builder-paths.json"
if [[ -z "${ANGELO_ALLOW_MISSING_GPU:-}" ]]; then
  "$PY" "$SOURCE/scripts/angelo/release_checks.py" gpu-sdks --source "$SOURCE" --platform linux \
    --record "$BUILD/angelo-release-build.json"
fi
echo "Linux release install: $INSTALL (package: scripts/angelo/linux_package.py stage --install $INSTALL --out ...)"
