#!/bin/sh
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Build and run the metadata reader's differential fuzzer under ASan and UBSan (macOS/clang).
#   tests/review/run_metadata_scan_reader_fuzz.sh [ITERATIONS] [SEED] [ZSTD_PREFIX]
# ZSTD_PREFIX holds include/zstd.h and lib/libzstd.a (Blender's lib/<platform>/zstd works).
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
ITERATIONS=${1:-20000}
SEED=${2:-1}
ZSTD=${3:-$ROOT/lib/macos_arm64/zstd}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# The reader before the change, in its own namespace.
GIT_LFS_SKIP_SMUDGE=1 git -C "$ROOT" show d2a83116da6^:source/blender/blenloader/intern/metadata_scan_reader.hh |
  sed 's/namespace blender::blo {/namespace blender::blo_old {/' > "$WORK/metadata_scan_reader_old.hh"
clang++ -std=c++20 -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined \
  -I"$WORK" -I"$ROOT/source/blender/blenloader/intern" -I"$ROOT/source/blender/blenloader" \
  -I"$ROOT/source/blender/blenlib" -I"$ZSTD/include" \
  "$ROOT/tests/review/metadata_scan_reader_fuzz.cc" "$ZSTD/lib/libzstd.a" -lz \
  -o "$WORK/fuzz"
"$WORK/fuzz" "$ITERATIONS" "$WORK/file.bin" "$SEED"
