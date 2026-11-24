#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# The Linux package on clean distributions (doc/angelo/releasing.rst, "Linux package"): in a fresh container of each
# test distribution (Ubuntu 22.04, the oldest; Ubuntu 24.04; Fedora), with only a graphical session's base client
# libraries added (GL/EGL with libglvnd's OpenGL, X11 with the X toolkit, Wayland, xkbcommon, fontconfig, D-Bus: what
# any GNOME or KDE desktop has), every library every ELF file of the package needs must resolve, and
# `angelo --background --factory-startup` must start and exit cleanly without writing anything inside the install.
#
# Two kinds of "not found" are not gaps, and are reported apart:
# - a library the package itself ships under that name: Python extension modules (MaterialX's) name their sibling
#   modules without a search path and are loaded after them, so the loader finds them by name at run time;
# - a GPU driver's or GPU runtime's own library (GPU_DRIVER_LIBS): the CUDA driver, ROCm's HIP runtime, Intel's Level
#   Zero loader. They come with the person's driver and are never bundled; only the GPU device modules that use them
#   (OpenImageDenoise's CUDA/HIP devices, the oneAPI adapter) need them, and only when that device is used.
# Needs podman or docker and network for the base images and packages.
#
#   linux_ldd_containers.sh Angelo-<v>-linux-x64.tar.xz [REPORT_DIR]
#
# Exit 1 when any distribution fails; REPORT_DIR (default: beside the tarball) gets one log per distribution.
set -euo pipefail
TARBALL="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
REPORTS="${2:-$(dirname "$TARBALL")/ldd-containers}"
ENGINE="${ANGELO_CONTAINER_ENGINE:-$(command -v podman || command -v docker || true)}"
[[ -n "$ENGINE" ]] || { echo "linux_ldd_containers: podman or docker is required" >&2; exit 1; }
mkdir -p "$REPORTS"
# image|command that adds a graphical session's base client libraries
DISTROS=(
  "docker.io/library/ubuntu:22.04|apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xz-utils libgl1 libegl1 libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libxkbcommon-x11-0 libsm6 libice6 libxt6 libopengl0 libwayland-client0 libwayland-cursor0 libwayland-egl1 libdecor-0-0 libfontconfig1 libdbus-1-3 >/dev/null"
  "docker.io/library/ubuntu:24.04|apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq xz-utils libgl1 libegl1 libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 libxkbcommon-x11-0 libsm6 libice6 libxt6 libopengl0 libwayland-client0 libwayland-cursor0 libwayland-egl1 libdecor-0-0 libfontconfig1 libdbus-1-3 >/dev/null"
  "registry.fedoraproject.org/fedora:43|dnf -y -q install xz mesa-libGL mesa-libEGL libX11 libXi libXxf86vm libXfixes libXrender libxkbcommon libxkbcommon-x11 libSM libICE libXt libglvnd-opengl libwayland-client libwayland-cursor libwayland-egl libdecor fontconfig dbus-libs findutils >/dev/null"
)
# A GPU driver's or runtime's own libraries (see above): the soname patterns, matched as shell globs.
GPU_DRIVER_LIBS='libcuda.so.* libnvidia-*.so.* libamdhip64.so.* libhsa-runtime64.so.* libze_loader.so.* libOpenCL.so.*'
# Inside: unpack as a person would, ldd every ELF file, then a background start.
INNER='set -e
mkdir -p /tmp/a && tar -xJf /pkg/'"$(basename "$TARBALL")"' -C /tmp/a
top=$(ls -d /tmp/a/*/)
find "$top" -type f -printf "%f\n" | sort -u > /tmp/shipped
missing=0
while IFS= read -r -d "" f; do
  if head -c 4 "$f" | grep -q "ELF"; then
    for name in $(ldd "$f" 2>&1 | awk "/not found/ {print \$1}" | sort -u); do
      if grep -qxF "$name" /tmp/shipped; then
        echo "SHIPPED ${f#$top}: $name (the package ships it; loaded by name after it)"
        continue
      fi
      driver=0
      set -f   # the patterns are globs for case, never file names here
      for pattern in '"$GPU_DRIVER_LIBS"'; do case "$name" in $pattern) driver=1;; esac; done
      set +f
      if [ "$driver" = 1 ]; then
        echo "DRIVER ${f#$top}: $name (from the GPU driver or runtime)"
      else
        echo "MISSING ${f#$top}: $name"
        missing=$((missing + 1))
      fi
    done
  fi
done < <(find "$top" -type f -print0)
echo "unresolved libraries: $missing"
touch /tmp/before-launch; sleep 1
HOME=/tmp/home "$top/angelo" --background --factory-startup --python-expr "import sys; print(\"ANGELO_STARTED\"); sys.exit(0)" 2>&1 | tail -20
written=$(find "$top" -newer /tmp/before-launch | wc -l)
echo "entries written inside the install by the launch: $written"
find "$top" -newer /tmp/before-launch | head -20
test "$missing" -eq 0 && test "$written" -eq 0'
failed=0
RESULTS=()
for entry in "${DISTROS[@]}"; do
  image="${entry%%|*}"; setup="${entry#*|}"
  name="$(echo "$image" | sed 's#.*/##; s#:#-#')"
  log="$REPORTS/$name.log"
  if "$ENGINE" run --rm -v "$(dirname "$TARBALL"):/pkg:ro,z" "$image" bash -c "$setup && $INNER" >"$log" 2>&1 \
      && grep -q ANGELO_STARTED "$log"; then
    echo "$name: ok"; RESULTS+=("\"$image\": \"ok\"")
  else
    echo "$name: FAIL (see $log)"; failed=1; RESULTS+=("\"$image\": \"fail\"")
  fi
done
# summary.json: {"package": ..., "distributions": {image: ok|fail}}, the release kit's receipt.
( IFS=,; printf '{"package": "%s", "distributions": {%s}}\n' "$(basename "$TARBALL")" "${RESULTS[*]}" ) \
  > "$REPORTS/summary.json"
exit "$failed"
