/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

/** \file
 * Angelo: whether two paths name the same file, as the file system decides (wm_files.cc, "the
 * document after a Restore"). Self-contained (no Blender headers), so its Windows branch is
 * syntax-checked on any host (test_native_windows_file_identity.py).
 */

#include <cstdint>
#include <optional>

namespace blender {

/** A file's identity on its volume. `kind` tells comparable identities apart: the 128-bit Windows
 * file ID, the 64-bit Windows file index, or POSIX `st_dev`/`st_ino`. */
struct WMAngeloFileIdentity {
  uint32_t kind = 0;
  uint64_t volume = 0, file_low = 0, file_high = 0;
  bool operator==(const WMAngeloFileIdentity &other) const
  {
    return kind == other.kind && volume == other.volume && file_low == other.file_low &&
           file_high == other.file_high;
  }
};

/** The identity of the existing file at `filepath` (UTF-8). None when it does not exist or its
 * identity is unknown: a zero volume serial number or file number is unknown, never a match. */
std::optional<WMAngeloFileIdentity> WM_angelo_file_identity(const char *filepath);

/** Whether two names (or paths, UTF-8) are the same name to the platform's file system: Unicode
 * canonical equivalence and case folding on macOS (APFS, HFS+), ordinal case-insensitivity on
 * Windows (NTFS, ReFS), the bytes elsewhere. */
bool WM_angelo_same_file_name(const char *a, const char *b);

}  // namespace blender
