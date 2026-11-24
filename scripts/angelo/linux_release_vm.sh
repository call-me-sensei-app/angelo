#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# The Linux release's one entry on the build VM (doc/angelo/releasing.rst, "Linux package"); the release kit calls
# these steps and reads OUT/receipts/<step>.json, nothing else:
#
#   linux_release_vm.sh build      SRC OUT   the release build in Blender's Rocky Linux 8 container
#   linux_release_vm.sh stage      SRC OUT   the final package tree, OUT/stage/<package> (sign it next)
#   linux_release_vm.sh pack-check SRC OUT   pack the signed tree, check it, and run it on clean distributions
#
# Inputs (environment): ANGELO_LIBDIR (lib/linux_x64 at the source's pin, from upstream Blender's library
# repository), ANGELO_AGENT_RUNTIME_DIR (lane C's Linux Codex), ANGELO_GPU_TOOLKITS (linux_gpu_toolkits_fetch.py's
# folder plus OptiX and DLSS headers); ANGELO_ALLOW_MISSING_GPU=1 / ANGELO_ALLOW_MISSING_RUNTIME=1 for a recorded
# non-release build; ANGELO_UNSIGNED=1 packs and checks without a signed manifest (development and CI only);
# ANGELO_RELEASE_KEYS=<file> verifies against a rehearsal's key list.
#
# Receipts: build.json (the build record), builder-paths.json, stage.json, pack.json, linux-package-check.json,
# ldd-containers.json. Exit codes: 0 done, 2 usage, 10 build, 20 stage, 30 pack, 31 package check, 32 clean
# distributions.
set -uo pipefail
STEP="${1:-}"; SRC="${2:-}"; OUT="${3:-}"
if [[ -z "$STEP" || -z "$SRC" || -z "$OUT" ]]; then
  echo "usage: linux_release_vm.sh build|stage|pack-check SRC OUT" >&2; exit 2
fi
SRC="$(cd "$SRC" && pwd)"; mkdir -p "$OUT/receipts"; OUT="$(cd "$OUT" && pwd)"
RECEIPTS="$OUT/receipts"
PY="${ANGELO_LIBDIR:-$SRC/lib/linux_x64}/python/bin/python3.13"
[[ -x "$PY" ]] || { echo "linux_release_vm: no Python 3.13 at $PY (set ANGELO_LIBDIR)" >&2; exit 2; }
SIGNED_ARGS=(); CHECK_ARGS=()
[[ "${ANGELO_UNSIGNED:-}" == "1" ]] && { SIGNED_ARGS=(--unsigned); CHECK_ARGS=(--allow-unsigned); }
[[ -n "${ANGELO_RELEASE_KEYS:-}" ]] && { SIGNED_ARGS+=(--keys "$ANGELO_RELEASE_KEYS"); CHECK_ARGS+=(--keys "$ANGELO_RELEASE_KEYS"); }
case "$STEP" in
  build)
    ANGELO_RELEASE_BUILD_DIR="$OUT/build" "$SRC/scripts/angelo/build_release_linux_container.sh" || exit 10
    cp "$OUT/build/angelo-release-build.json" "$RECEIPTS/build.json" || exit 10
    cp "$OUT/build/angelo-builder-paths.json" "$RECEIPTS/builder-paths.json" || exit 10
    ;;
  stage)
    rm -rf "$OUT/stage"
    "$PY" "$SRC/scripts/angelo/linux_package.py" stage --install "$OUT/build/install" --out "$OUT/stage" || exit 20
    cp "$OUT/stage/stage-receipt.json" "$RECEIPTS/stage.json" || exit 20
    ;;
  pack-check)
    rm -rf "$OUT/dist"
    "$PY" "$SRC/scripts/angelo/linux_package.py" pack --stage "$OUT/stage" --out "$OUT/dist" \
      --debug-symbols "$OUT/build/debug-symbols" "${SIGNED_ARGS[@]}" || exit 30
    cp "$OUT/dist/pack-receipt.json" "$RECEIPTS/pack.json" || exit 30
    TARBALL="$OUT/dist/$("$PY" -c 'import json, sys; print(json.load(open(sys.argv[1]))["tarball"])' "$RECEIPTS/pack.json")"
    "$PY" "$SRC/scripts/angelo/linux_package.py" check "$TARBALL" --launch "${CHECK_ARGS[@]}" \
      --upstream-root "${ANGELO_LIBDIR:-$SRC/lib/linux_x64}" \
      --report "$RECEIPTS/linux-package-check.json" || exit 31
    "$SRC/scripts/angelo/linux_ldd_containers.sh" "$TARBALL" "$OUT/dist/ldd-containers" || {
      cp "$OUT/dist/ldd-containers/summary.json" "$RECEIPTS/ldd-containers.json" 2>/dev/null; exit 32; }
    cp "$OUT/dist/ldd-containers/summary.json" "$RECEIPTS/ldd-containers.json" || exit 32
    ;;
  *) echo "usage: linux_release_vm.sh build|stage|pack-check SRC OUT" >&2; exit 2 ;;
esac
echo "linux_release_vm: $STEP done; receipts in $RECEIPTS"
