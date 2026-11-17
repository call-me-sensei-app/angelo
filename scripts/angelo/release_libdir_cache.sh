#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# release_libdir_cache.sh check|record BUILD LIBDIR COMMIT [PINNED]: keep a release build folder's CMake cache only while
# it was configured against these libraries, and refuse a configure that still names others (build_release_macos.sh;
# windows_release_toolchain.ps1's Reset-BuildDirForLibraries and Confirm-BuildDirLibraries are the Windows twins).
#
# A build folder configured against other libraries keeps what CMake found in them (USD_LIBRARY, USD_INCLUDE_DIR and
# every other find result) and never looks again when LIBDIR changes: after a library update (a Blender bump) the
# executable compiled and linked against the old libraries while the bundle installed the new ones, and dyld refused
# it (the 1.1 rehearsal, 2026-11-04). "check", before the configure: when the folder or the commit of the libraries
# differs from the last configure's record (BUILD/angelo-libdir.txt), CMakeCache.txt moves to
# CMakeCache.txt.libdir-changed; compiled objects stay, so only what the new libraries change rebuilds. A folder from
# before that record compares the folder alone. "record", after a configure that succeeded: fails (exit 1, naming them)
# when LIBDIR is not at the source's pin (PINNED) or a cached path lies in another library checkout (a folder named like
# LIBDIR, such as another macos_arm64), since the build would compile or link against it; otherwise writes the record.
set -euo pipefail
ACTION="${1:?check or record}"; BUILD="${2:?build folder}"; LIBDIR="${3:?libraries folder}"; COMMIT="${4:-}"; PINNED="${5:-}"
RECORD="$BUILD/angelo-libdir.txt"
CACHE="$BUILD/CMakeCache.txt"
real_dir() { (cd "$1" 2>/dev/null && pwd -P) || printf '%s\n' "$1"; }
# Every cached path (values split at ';', '[', ']' and '"', as lists and FIND_PACKAGE_MESSAGE_DETAILS write them) inside a
# folder named like LIBDIR that is not LIBDIR: "  KEY = path" lines.
# A function, not inline in $(...): macOS's bash 3.2 misreads a case pattern's ')' inside a command substitution.
other_libraries() {
  local name root key value item prefix
  name="$(basename "$LIBDIR")"; root="$(real_dir "$LIBDIR")"
  sed -n 's/^\([^#/][^:=]*\):[A-Z]*=\(.*\)$/\1 \2/p' "$CACHE" | while read -r key value; do
    printf '%s\n' "$value" | tr ';[]"' '\n\n\n\n' | while IFS= read -r item; do
      case "$item" in
        */"$name"/*) prefix="${item%%/"$name"/*}/$name" ;;
        */"$name") prefix="$item" ;;
        *) continue ;;
      esac
      [[ "$(real_dir "$prefix")" == "$root" ]] || printf '  %s = %s\n' "$key" "$item"
    done
  done
}

case "$ACTION" in
  record)
    if [[ -n "$PINNED" && "$COMMIT" != "$PINNED" ]]; then
      echo "The libraries in $LIBDIR are at ${COMMIT:0:12}; this source pins ${PINNED:0:12}. Check out the pinned libraries." >&2
      exit 1
    fi
    others="$(other_libraries)"
    if [[ -n "$others" ]]; then
      echo "This configure still names libraries outside $LIBDIR (the build would compile or link against them):" >&2
      printf '%s\n' "$others" | head -n 20 >&2
      echo "Move $CACHE aside and configure again." >&2
      exit 1
    fi
    printf '%s\n%s\n' "$LIBDIR" "$COMMIT" > "$RECORD"
    ;;
  check)
    [[ -f "$CACHE" ]] || exit 0
    cached="$(sed -n 's/^LIBDIR:[A-Z]*=//p' "$CACHE" | head -n 1)"
    recorded="$(sed -n 2p "$RECORD" 2>/dev/null || true)"
    stale=""
    if [[ -n "$cached" && "$(real_dir "$cached")" != "$(real_dir "$LIBDIR")" ]]; then
      stale="configured with $cached"
    elif [[ -n "$recorded" && "$recorded" != "$COMMIT" ]]; then
      stale="configured with the libraries at ${recorded:0:12}, now ${COMMIT:0:12}"
    fi
    if [[ -n "$stale" ]]; then
      mv -f "$CACHE" "$CACHE.libdir-changed"
      echo "The build folder was $stale: its CMake cache moved to CMakeCache.txt.libdir-changed; configuring anew."
    fi
    ;;
  *)
    echo "usage: release_libdir_cache.sh check|record BUILD LIBDIR COMMIT [PINNED]" >&2
    exit 2
    ;;
esac
