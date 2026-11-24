#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

# Build the agent runtime (the patched Codex) for Linux x86-64 from a checkout of the Codex fork: upstream's release
# profile for the pin's rust_target (agent_runtime.json, platforms.linux), every program the pin requires, then
# `strip --strip-unneeded` (section headers stay, so the release check can read the glibc versions each binary
# needs). The debug info goes to OUT_DIR.debug/ for offline symbolication; it is never bundled.
# See doc/angelo/releasing.rst, "Agent runtime".
#
#   build_agent_runtime_linux.sh FORK_CHECKOUT OUT_DIR
#
# Build on a system whose glibc is the pin's glibc_ceiling (Rocky Linux 8, as upstream Blender builds Linux), for
# example inside `rockylinux:8` with rustup, gcc-toolset, clang, cmake, perl and protobuf; a newer system links newer
# glibc symbol versions and the check at the end refuses the result. V8 is OpenAI's prebuilt for the target (below).
# The finished folder is checked with `release_checks.py agent-runtime OUT_DIR --platform linux`, which also
# runs codex (version, schema, wires, capabilities; offline, with a temporary CODEX_HOME).
set -euo pipefail
SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
PIN="$SOURCE/scripts/addons_core/angelo_agent/agent_runtime.json"
python3 "$SOURCE/scripts/angelo/load_guard.py" "agent runtime (Codex) build" || exit $?
if [[ $# -ne 2 ]]; then
  echo "usage: $0 FORK_CHECKOUT OUT_DIR" >&2
  exit 2
fi
if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "x86_64" ]]; then
  echo "This recipe is for Linux x86-64; doc/angelo/releasing.rst describes the macOS and Windows builds." >&2
  exit 1
fi
FORK="$(cd "$1" && pwd)"
OUT="$(mkdir -p "$2" && cd "$2" && pwd)"
read -r TARGET COMMIT BINARIES < <(python3 - "$PIN" <<'EOF'
import json, sys
pin = json.load(open(sys.argv[1], encoding="utf-8"))
linux = pin["platforms"]["linux"]
print(linux["rust_target"], pin["fork"]["commit"], " ".join(linux["required"] + linux["optional"]))
EOF
)
HEAD="$(git -C "$FORK" rev-parse HEAD)"
if [[ "$HEAD" != "$COMMIT" ]]; then
  echo "The fork checkout is at $HEAD; agent_runtime.json pins $COMMIT. Check out the pinned commit first." >&2
  exit 1
fi
BIN_ARGS=()
for binary in $BINARIES; do
  BIN_ARGS+=(--bin "$binary")
done
# The fork's V8 (rusty_v8 with the ptrcomp_sandbox profile) has no denoland prebuilt: as upstream's release does
# (.github/actions/setup-rusty-v8), take OpenAI's rusty-v8 release artifacts for the target, checked against the
# checksum manifest the fork carries (third_party/v8), and point the v8 crate at them.
V8="$OUT.v8"
mkdir -p "$V8"
V8_VERSION="$(cd "$FORK" && python3 .github/scripts/rusty_v8_bazel.py resolved-v8-crate-version)"
V8_URL="https://github.com/openai/codex/releases/download/rusty-v8-v${V8_VERSION}"
V8_PROFILE=ptrcomp_sandbox_release
V8_SUMS="rusty_v8_${V8_PROFILE}_${TARGET}.sha256"
V8_TRUSTED="$FORK/third_party/v8/rusty_v8_${V8_VERSION//./_}_release_manifests.sha256"
curl -fsSL "$V8_URL/$V8_SUMS" -o "$V8/$V8_SUMS"
EXPECTED="$(grep -F "  $V8_SUMS" "$V8_TRUSTED" | cut -d ' ' -f 1)"
ACTUAL="$(sha256sum "$V8/$V8_SUMS" | cut -d ' ' -f 1)"
if [[ -z "$EXPECTED" || "$EXPECTED" != "$ACTUAL" ]]; then
  echo "V8 checksum manifest $V8_SUMS does not match the fork's $V8_TRUSTED" >&2
  exit 1
fi
curl -fsSL "$V8_URL/librusty_v8_${V8_PROFILE}_${TARGET}.a.gz" -o "$V8/librusty_v8_${V8_PROFILE}_${TARGET}.a.gz"
curl -fsSL "$V8_URL/src_binding_${V8_PROFILE}_${TARGET}.rs" -o "$V8/src_binding_${V8_PROFILE}_${TARGET}.rs"
(cd "$V8" && tr -d '\r' < "$V8_SUMS" | sha256sum --check -)
export RUSTY_V8_ARCHIVE="$V8/librusty_v8_${V8_PROFILE}_${TARGET}.a.gz"
export RUSTY_V8_SRC_BINDING_PATH="$V8/src_binding_${V8_PROFILE}_${TARGET}.rs"
# OpenSSL linked statically: distributions ship different sonames (Rocky 8 libssl.so.1.1, Ubuntu 22.04+ and Fedora
# libssl.so.3), so a dynamic link starts only where it was built. The version, checksum and configuration are the
# fork's own (its musl release script), read from the fork so they move with it.
SSL_SCRIPT="$FORK/.github/scripts/install-musl-openssl.sh"
SSL_VERSION="$(sed -n 's/^openssl_version="\(.*\)"$/\1/p' "$SSL_SCRIPT")"
SSL_SHA256="$(sed -n 's/^openssl_sha256="\(.*\)"$/\1/p' "$SSL_SCRIPT")"
SSL="$OUT.openssl"
if [[ -z "$SSL_VERSION" || -z "$SSL_SHA256" ]]; then
  echo "Could not read the OpenSSL pin from $SSL_SCRIPT" >&2
  exit 1
fi
# Installed with DESTDIR under the generic /usr/local prefix: OpenSSL compiles its prefix into the library (its
# modules and engines folders), which must name no folder of this build.
SSL_PREFIX="$SSL/root/usr/local"
if [[ ! -f "$SSL_PREFIX/.complete" ]]; then
  mkdir -p "$SSL"
  curl -fsSL "https://github.com/openssl/openssl/releases/download/openssl-$SSL_VERSION/openssl-$SSL_VERSION.tar.gz" \
    -o "$SSL/openssl.tar.gz"
  echo "$SSL_SHA256  $SSL/openssl.tar.gz" | sha256sum -c -
  tar -xzf "$SSL/openssl.tar.gz" -C "$SSL"
  (
    cd "$SSL/openssl-$SSL_VERSION"
    perl ./Configure linux-x86_64 --prefix=/usr/local --openssldir=/usr/local/ssl --libdir=lib \
      no-shared no-module no-tests no-comp no-zlib no-zlib-dynamic \
      no-ssl3 no-md2 no-rc5 no-weak-ssl-ciphers no-camellia no-idea no-seed \
      no-engine no-async -DOPENSSL_NO_SECURE_MEMORY
    make -j"$(nproc)" build_libs > "$SSL/build.log"
    make install_dev DESTDIR="$SSL/root" > "$SSL/install.log"
  )
  touch "$SSL_PREFIX/.complete"
fi
TARGET_ENV="$(echo "$TARGET" | tr 'a-z-' 'A-Z_')"
export "${TARGET_ENV}_OPENSSL_DIR=$SSL_PREFIX" "${TARGET_ENV}_OPENSSL_NO_VENDOR=1" "${TARGET_ENV}_OPENSSL_STATIC=1"
# No folder of this build in the binaries: Rust's panic locations and the C dependencies' __FILE__ name the fork, the
# Cargo and rustup homes and the builder's home by neutral names (release_checks builder-paths checks the result).
CARGO_DIR="${CARGO_HOME:-$HOME/.cargo}"
RUSTUP_DIR="${RUSTUP_HOME:-$HOME/.rustup}"
REMAP=""
PREFIX_MAP=""
# rustc and GCC apply the LAST matching map: the builder's home goes first, so the folders inside it still get theirs.
for pair in "$HOME=~" "$FORK=codex" "$CARGO_DIR=cargo" "$RUSTUP_DIR=rustup" "$OUT=out"; do
  REMAP="$REMAP --remap-path-prefix=$pair"
  PREFIX_MAP="$PREFIX_MAP -ffile-prefix-map=$pair"
done
export RUSTFLAGS="${RUSTFLAGS:-}$REMAP"
export CFLAGS="${CFLAGS:-}$PREFIX_MAP" CXXFLAGS="${CXXFLAGS:-}$PREFIX_MAP"
cd "$FORK/codex-rs"
STABLE_GIT_COMMIT="$HEAD" CARGO_NET_GIT_FETCH_WITH_CLI=true \
  cargo build --target "$TARGET" --release "${BIN_ARGS[@]}"
mkdir -p "$OUT" "$OUT.debug"
for binary in $BINARIES; do
  cp "target/$TARGET/release/$binary" "$OUT/$binary"
  objcopy --only-keep-debug "$OUT/$binary" "$OUT.debug/$binary.debug"
  strip --strip-unneeded "$OUT/$binary"
  objcopy --add-gnu-debuglink="$OUT.debug/$binary.debug" "$OUT/$binary"
done
python3 "$SOURCE/scripts/angelo/release_checks.py" agent-runtime "$OUT" --platform linux
python3 "$SOURCE/scripts/angelo/release_checks.py" builder-paths "$OUT"
echo "Agent runtime build of $(git rev-parse --short HEAD) for $TARGET: $(cd "$OUT" && pwd)"
