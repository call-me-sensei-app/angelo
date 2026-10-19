/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace blender {

enum class BLOMetadataScanStatus { OK, INVALID, LIMIT_EXCEEDED, CANCELLED, IO_ERROR };

/** Explicit admission policy; zero limits are invalid. No file-size-derived allocation limits. */
struct BLOMetadataScanLimits {
  /**
   * Decoded address space traversed, summed over all passes (including plain-file seeks).
   * A failing compressed-footer overflow probe can decode one additional byte; it is reported
   * in the counter and no metadata is returned. Successful scans never exceed this limit.
   */
  uint64_t decoded_bytes = 0;
  /**
   * Encoded bytes actually read, summed over all passes. Plain files are read in 64 KiB windows,
   * so this includes the rest of a window a skipped payload began in; a window never extends past
   * the bytes still allowed, so read-ahead alone never exceeds this limit.
   */
  uint64_t input_bytes = 0;
  uint64_t block_count = 0;
  uint64_t block_bytes = 0;
  uint32_t dna_bytes = 0;
  uint32_t dna_entries = 0;
  /** Maximum simultaneous codec-owned allocation, including allocation headers. */
  uint64_t decoder_bytes = 0;
  uint64_t zstd_window_bytes = 0;
  /** Total retained names/paths, bounding both requests and output allocations. */
  uint32_t result_names = 0;
};

struct BLOMetadataScanRequest {
  /** Empty means a summary of all linkable types. Unknown types produce no matching row. */
  std::string list;
  uint64_t offset = 0;
  uint32_t limit = 0;
  uint32_t summary_names = 0;
  uint32_t libraries_limit = 0;
};

struct BLOMetadataScanType {
  std::string list_name;
  uint64_t total = 0;
  std::vector<std::string> names;
};

struct BLOMetadataScanResult {
  BLOMetadataScanStatus status = BLOMetadataScanStatus::INVALID;
  /** Stable diagnostic text; never includes a pathname or data from the file. */
  std::string message;
  int file_version = 0;
  int subversion = 0;
  std::vector<BLOMetadataScanType> types;
  std::vector<std::string> library_paths;
  uint64_t decoded_bytes = 0;
  uint64_t input_bytes = 0;
  uint64_t blocks = 0;
  uint32_t passes = 0;
};

using BLOMetadataScanCancelled = bool (*)(void *userdata);

/**
 * Read only metadata through an already-admitted regular-file descriptor; never reopen its path.
 * The descriptor remains caller-owned and must be private to this request. POSIX uses positioned
 * reads; Windows preserves/restores its initial cursor but changes it during the operation.
 * Caller must check file identity, size, mtime, ctime and link count after completion.
 *
 * No live Main, data-block loading, asset parsing, linking or callbacks into Python are used.
 * The cancellation callback must be thread-safe and must not require Python's GIL.
 *
 * Names follow libraries.load's reverse-file order and packed-ID filtering. Summary/first-page
 * reads use two streaming passes; deeper pages use three to avoid retaining offset + limit names.
 * Geometry payloads are skipped (plain: past the 64 KiB read window, without reading) or
 * discarded in fixed chunks (compressed). The scanner does not allocate seek tables or entire
 * compressed frames, including for seekable Zstandard. The cancellation callback is asked at every
 * block and every read, not at every budget charge.
 */
BLOMetadataScanResult BLO_metadata_scan_from_descriptor(
    int descriptor,
    const BLOMetadataScanRequest &request,
    const BLOMetadataScanLimits &limits,
    BLOMetadataScanCancelled cancelled = nullptr,
    void *userdata = nullptr);

}  // namespace blender
