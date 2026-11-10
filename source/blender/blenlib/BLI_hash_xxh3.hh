/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bli
 *
 * Streaming 128-bit XXH3 (xxHash 0.8) over bytes fed in any number of pieces.
 *
 * The digest is identical however the input is split, so a writer can hash each buffer as it is
 * accepted and a reader can hash the same file in its own chunk size. XXH3 is not cryptographic:
 * it detects accidental and foreign changes, not an attacker who can choose the replacement bytes.
 * xxHash itself stays a private dependency of blenlib; other modules use this class.
 */

#include <array>
#include <cstddef>
#include <cstdint>

#include "BLI_span.hh"

namespace blender {

class XXH3_128Stream {
 private:
  /** The xxHash streaming state (opaque here, so the header does not expose xxhash.h). */
  void *state_ = nullptr;
  uint64_t bytes_ = 0;

 public:
  XXH3_128Stream();
  ~XXH3_128Stream();
  XXH3_128Stream(const XXH3_128Stream &) = delete;
  XXH3_128Stream &operator=(const XXH3_128Stream &) = delete;
  XXH3_128Stream(XXH3_128Stream &&other) noexcept;
  XXH3_128Stream &operator=(XXH3_128Stream &&other) noexcept;

  /** Append bytes to the hashed stream. */
  void update(Span<std::byte> bytes);
  void update(const void *data, size_t size);
  /** The number of bytes appended so far. */
  uint64_t bytes() const
  {
    return bytes_;
  }
  /** The digest of everything appended so far, in xxHash's canonical (big-endian) byte order.
   * The stream is unchanged, so it may continue afterwards. */
  std::array<uint8_t, 16> finalize() const;
};

/** One-shot 128-bit XXH3 of a buffer, equal to a stream fed the same bytes. */
std::array<uint8_t, 16> BLI_hash_xxh3_128(Span<std::byte> bytes);

}  // namespace blender
