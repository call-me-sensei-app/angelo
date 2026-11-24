#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# Run build_release_linux.sh in Blender's Linux release environment (Rocky Linux 8 + gcc-toolset-14) on any Linux
# host with podman or docker. doc/angelo/releasing.rst, "Linux package".
#
# The image is built once from build_files/build_environment/linux/angelo_rocky8.Containerfile (upstream's
# linux_rocky8_setup.sh) and tagged with that file's and the setup script's digest, so a changed environment builds a
# new image. The source, libraries, agent runtime, GPU toolkits and build folder are mounted at the SAME paths inside
# the container, so the paths CMake records and the builder-paths check's identity paths are the host's. The build runs
# as the calling user, so every file it writes is the caller's. The same variables as build_release_linux.sh apply.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
ENGINE="${ANGELO_CONTAINER_ENGINE:-$(command -v podman || command -v docker || true)}"
[[ -n "$ENGINE" ]] || { echo "build_release_linux_container: podman or docker is required" >&2; exit 1; }
ENV_DIR="$SOURCE/build_files/build_environment/linux"
DIGEST="$(cat "$ENV_DIR/angelo_rocky8.Containerfile" "$ENV_DIR/linux_rocky8_setup.sh" | sha256sum | cut -c1-12)"
IMAGE="angelo-linux-build:rocky8-$DIGEST"
if ! "$ENGINE" image inspect "$IMAGE" >/dev/null 2>&1; then
  "$ENGINE" build -t "$IMAGE" -f "$ENV_DIR/angelo_rocky8.Containerfile" "$ENV_DIR"
fi
BUILD="${ANGELO_RELEASE_BUILD_DIR:-$SOURCE/../build-angelo-linux-release}"
mkdir -p "$BUILD"
BUILD="$(cd "$BUILD" && pwd)"
# A linked worktree's .git names the main checkout's git folder: mount that too.
GIT_COMMON="$(cd "$SOURCE" && cd "$(git rev-parse --git-common-dir)" && pwd)"
MOUNTS=()
for dir in "$SOURCE" "$GIT_COMMON" "$BUILD" ${ANGELO_LIBDIR:+"$ANGELO_LIBDIR"} \
           ${ANGELO_AGENT_RUNTIME_DIR:+"$ANGELO_AGENT_RUNTIME_DIR"} ${ANGELO_GPU_TOOLKITS:+"$ANGELO_GPU_TOOLKITS"}; do
  real="$(cd "$dir" && pwd)"
  MOUNTS+=(-v "$real:$real:z")
done
USER_ARGS=(--user "$(id -u):$(id -g)")
[[ "$(basename "$ENGINE")" == "podman" ]] && USER_ARGS=(--userns=keep-id)
ENV_ARGS=()
for name in ANGELO_AGENT_RUNTIME_DIR ANGELO_GPU_TOOLKITS ANGELO_ALLOW_MISSING_GPU ANGELO_ALLOW_MISSING_RUNTIME ANGELO_LIBDIR ANGELO_BUILD_JOBS \
            ANGELO_RELEASE_CONFIGURE_ONLY SOURCE_DATE_EPOCH; do
  [[ -n "${!name:-}" ]] && ENV_ARGS+=(-e "$name=${!name}")
done
exec "$ENGINE" run --rm "${USER_ARGS[@]}" "${MOUNTS[@]}" "${ENV_ARGS[@]}" \
  -e ANGELO_RELEASE_BUILD_DIR="$BUILD" -e HOME=/tmp -e ANGELO_HOST_HOME="$HOME" -e ANGELO_HOST_USER="$(id -un)" \
  -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
  -w "$SOURCE" "$IMAGE" \
  bash -c 'source /opt/rh/gcc-toolset-14/enable && exec scripts/angelo/build_release_linux.sh'
