#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# Build the agent runtime (the patched Codex) for macOS arm64 from a checkout of the Codex fork, as the fork's
# ANGELO.md says: upstream's release profile (.github/workflows/rust-release.yml) with packed dSYMs, then
# `strip -S -x`. See doc/angelo/releasing.rst, "Agent runtime".
#
#   build_agent_runtime_macos.sh FORK_CHECKOUT OUT_DIR [BINARY ...]
#
# BINARY defaults to codex; the code-mode host is upstream's own build of the same release, so name
# codex-code-mode-host only to build it here too (that also needs the prebuilt V8 archive the v8 crate downloads).
# Check the finished folder with `release_checks.py agent-runtime OUT_DIR --platform macos`.
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
# Never beside another run's timed benchmark trial: refused while it holds this machine's lock (load_guard.py).
python3 "$SOURCE/scripts/angelo/load_guard.py" "agent runtime (Codex) build" || exit $?
if [[ $# -lt 2 ]]; then
  echo "usage: $0 FORK_CHECKOUT OUT_DIR [BINARY ...]" >&2
  exit 2
fi
if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "This recipe is for macOS arm64; doc/angelo/releasing.rst describes the Windows build." >&2
  exit 1
fi
FORK="$(cd "$1" && pwd)"
OUT="$2"
shift 2
BINARIES=("$@")
[[ ${#BINARIES[@]} -gt 0 ]] || BINARIES=(codex)
BIN_ARGS=()
for binary in "${BINARIES[@]}"; do
  BIN_ARGS+=(--bin "$binary")
done
cd "$FORK/codex-rs"
STABLE_GIT_COMMIT="$(git rev-parse HEAD)" CARGO_PROFILE_RELEASE_SPLIT_DEBUGINFO=packed \
  CARGO_NET_GIT_FETCH_WITH_CLI=true cargo build --target aarch64-apple-darwin --release "${BIN_ARGS[@]}"
mkdir -p "$OUT"
for binary in "${BINARIES[@]}"; do
  cp "target/aarch64-apple-darwin/release/$binary" "$OUT/$binary"
  strip -S -x "$OUT/$binary"
done
echo "Agent runtime build of $(git rev-parse --short HEAD): $(cd "$OUT" && pwd)"
