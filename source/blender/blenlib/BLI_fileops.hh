/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bli
 * \brief File and directory operations.
 */

#pragma once

#include "BLI_fileops.h"  // IWYU pragma: export
#include "BLI_string_ref.hh"

#include <cstdint>
#include <fstream>
#include <string>

namespace blender {

/**
 * Angelo: a file's identity as CPython's `os.stat` reports it, so Python can compare it with its
 * own stat of that file later: `st_dev` (`device`; `device_signed` for `NODEV`), `st_ino`
 * (`(index_high << 64) | index_low`), `st_size`, `st_mtime_ns` and, on macOS, `st_birthtime_ns`
 * (none on Windows). On Windows that is the 64-bit volume serial of `FILE_ID_INFO` where the
 * volume answers it, and its 128-bit file ID where that is nonzero (else the 64-bit file index),
 * as CPython 3.13 takes them; without `FILE_ID_INFO`, the 32-bit serial and the file index.
 */
struct FileIdentity {
  uint64_t device = 0;
  bool device_signed = false;
  uint64_t index_low = 0;
  uint64_t index_high = 0;
  int64_t size = 0;
  int64_t modified_ns = 0;
  int64_t created_ns = 0;
};

/**
 * The identity of the file at `filepath` (never following a link at its name); false when it
 * can't be asked.
 */
bool BLI_file_identity(const char *filepath, FileIdentity *r_identity);

/**
 * The file at `filepath` held open (never following a link at its name), so its identity can be
 * taken after it is renamed (#BLI_file_identity_held): a rename keeps the file, not necessarily
 * its identity on every file system (FAT on macOS numbers a file by its directory entry). On
 * Windows the handle shares delete, so the file can still be renamed. Null when it can't be
 * opened.
 */
void *BLI_file_identity_hold(const char *filepath);
/** The identity of a held file now; false for a null `held` or when it can't be asked. */
bool BLI_file_identity_held(const void *held, FileIdentity *r_identity);
/**
 * Whether a held file is known to be at no name any more: unlinked, or replaced by another file
 * renamed over it (on Windows: deleted, or delete pending). False for a null `held` or when it
 * can't be asked. Unlike its identity, this never depends on how a file system numbers files.
 */
bool BLI_file_identity_held_unlinked(const void *held);
/** Close a held file (null: nothing). */
void BLI_file_identity_release(void *held);
/**
 * The identity of the file open as the C runtime descriptor `file` (a writer's, before it closes
 * it); false when it can't be asked.
 */
bool BLI_file_identity_descriptor(int file, FileIdentity *r_identity);
/** Whether `a` and `b` name the same file (device and file ID; never its size or times). */
bool BLI_file_identity_same_file(const FileIdentity &a, const FileIdentity &b);

/**
 * std::fstream subclass that handles UTF16 encoding on Windows.
 *
 * For documentation, see https://en.cppreference.com/w/cpp/io/basic_fstream
 */
class fstream : public std::fstream {
 public:
  fstream() = default;
  explicit fstream(const char *filepath,
                   std::ios_base::openmode mode = ios_base::in | ios_base::out);
  explicit fstream(const std::string &filepath,
                   std::ios_base::openmode mode = ios_base::in | ios_base::out);

  void open(StringRefNull filepath, ios_base::openmode mode = ios_base::in | ios_base::out);
};

}  // namespace blender
