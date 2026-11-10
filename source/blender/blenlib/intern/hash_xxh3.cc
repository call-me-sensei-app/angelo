/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bli
 */

#include <cstdlib>
#include <utility>

#include <xxhash.h>

#include "BLI_assert.h"
#include "BLI_hash_xxh3.hh"

namespace blender {

static XXH3_state_t *xxh3_state(void *state)
{
  return static_cast<XXH3_state_t *>(state);
}

static std::array<uint8_t, 16> xxh3_canonical(const XXH128_hash_t hash)
{
  XXH128_canonical_t canonical;
  XXH128_canonicalFromHash(&canonical, hash);
  std::array<uint8_t, 16> digest;
  static_assert(sizeof(canonical.digest) == 16);
  for (int i = 0; i < 16; i++) {
    digest[i] = canonical.digest[i];
  }
  return digest;
}

XXH3_128Stream::XXH3_128Stream()
{
  XXH3_state_t *state = XXH3_createState();
  if (state == nullptr || XXH3_128bits_reset(state) != XXH_OK) {
    /* Allocation of the fixed-size state failed: like any other allocation in Blender. */
    std::abort();
  }
  state_ = state;
}

XXH3_128Stream::~XXH3_128Stream()
{
  if (state_) {
    XXH3_freeState(xxh3_state(state_));
  }
}

XXH3_128Stream::XXH3_128Stream(XXH3_128Stream &&other) noexcept
    : state_(std::exchange(other.state_, nullptr)), bytes_(std::exchange(other.bytes_, 0))
{
}

XXH3_128Stream &XXH3_128Stream::operator=(XXH3_128Stream &&other) noexcept
{
  if (this != &other) {
    if (state_) {
      XXH3_freeState(xxh3_state(state_));
    }
    state_ = std::exchange(other.state_, nullptr);
    bytes_ = std::exchange(other.bytes_, 0);
  }
  return *this;
}

void XXH3_128Stream::update(const void *data, const size_t size)
{
  BLI_assert(state_ != nullptr);
  if (size == 0) {
    return;
  }
  XXH3_128bits_update(xxh3_state(state_), data, size);
  bytes_ += uint64_t(size);
}

void XXH3_128Stream::update(const Span<std::byte> bytes)
{
  this->update(bytes.data(), size_t(bytes.size()));
}

std::array<uint8_t, 16> XXH3_128Stream::finalize() const
{
  BLI_assert(state_ != nullptr);
  return xxh3_canonical(XXH3_128bits_digest(xxh3_state(state_)));
}

std::array<uint8_t, 16> BLI_hash_xxh3_128(const Span<std::byte> bytes)
{
  return xxh3_canonical(XXH3_128bits(bytes.data(), size_t(bytes.size())));
}

}  // namespace blender
