/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <algorithm>
#include <string>
#include <vector>

#include "BLI_hash_xxh3.hh"

namespace blender::tests {

static std::string hex(const std::array<uint8_t, 16> &digest)
{
  static const char *digits = "0123456789abcdef";
  std::string text;
  for (const uint8_t byte : digest) {
    text += digits[byte >> 4];
    text += digits[byte & 15];
  }
  return text;
}

static Span<std::byte> as_bytes(const std::vector<uint8_t> &data)
{
  return Span<std::byte>(reinterpret_cast<const std::byte *>(data.data()), int64_t(data.size()));
}

static std::vector<uint8_t> pattern(const size_t size)
{
  std::vector<uint8_t> data(size);
  uint32_t state = 2463534242u;
  for (uint8_t &byte : data) {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    byte = uint8_t(state);
  }
  return data;
}

TEST(hash_xxh3, KnownVectorsInCanonicalOrder)
{
  /* xxHash 0.8's published XXH128 of empty input (seed 0), as `xxhsum -H2` prints it:
   * high 64 bits then low 64 bits, big-endian. */
  EXPECT_EQ(hex(XXH3_128Stream().finalize()), "99aa06d3014798d86001c324468d497f");
  EXPECT_EQ(hex(BLI_hash_xxh3_128({})), "99aa06d3014798d86001c324468d497f");
}

TEST(hash_xxh3, ChunkedEqualsOneShotForEverySplit)
{
  /* Sizes around XXH3's internal block and stripe boundaries (16, 128, 240, 1024 bytes and its
   * 256-byte internal buffer), plus a multi-megabyte stream. */
  for (const size_t size : {size_t(0), size_t(1), size_t(16), size_t(17), size_t(128), size_t(129),
                            size_t(240), size_t(241), size_t(1024), size_t(1025), size_t(4096 + 7),
                            size_t(3 * 1024 * 1024 + 11)})
  {
    const std::vector<uint8_t> data = pattern(size);
    const std::array<uint8_t, 16> expected = BLI_hash_xxh3_128(as_bytes(data));
    for (const size_t step : {size_t(1), size_t(7), size_t(64), size_t(255), size_t(4096),
                              size_t(1024 * 1024)})
    {
      if (size > 64 * 1024 && step < 64) {
        continue; /* Byte-at-a-time over megabytes adds time, not coverage. */
      }
      XXH3_128Stream stream;
      for (size_t offset = 0; offset < size; offset += step) {
        stream.update(as_bytes(data).slice(int64_t(offset), int64_t(std::min(step, size - offset))));
      }
      EXPECT_EQ(stream.bytes(), uint64_t(size));
      EXPECT_EQ(stream.finalize(), expected) << "size " << size << " step " << step;
    }
  }
}

TEST(hash_xxh3, FinalizeLeavesTheStreamUsable)
{
  const std::vector<uint8_t> data = pattern(1000);
  XXH3_128Stream stream;
  stream.update(as_bytes(data).take_front(400));
  EXPECT_EQ(stream.finalize(), BLI_hash_xxh3_128(as_bytes(data).take_front(400)));
  stream.update(as_bytes(data).drop_front(400));
  EXPECT_EQ(stream.finalize(), BLI_hash_xxh3_128(as_bytes(data)));
  EXPECT_EQ(stream.finalize(), stream.finalize());
}

TEST(hash_xxh3, OneChangedByteChangesTheDigest)
{
  std::vector<uint8_t> data = pattern(1 << 20);
  const std::array<uint8_t, 16> original = BLI_hash_xxh3_128(as_bytes(data));
  for (const size_t at : {size_t(0), size_t(12345), data.size() - 1}) {
    data[at] ^= 1;
    EXPECT_NE(BLI_hash_xxh3_128(as_bytes(data)), original) << "byte " << at;
    data[at] ^= 1;
  }
  EXPECT_EQ(BLI_hash_xxh3_128(as_bytes(data)), original);
}

TEST(hash_xxh3, MovedStreamKeepsItsState)
{
  const std::vector<uint8_t> data = pattern(300);
  XXH3_128Stream first;
  first.update(as_bytes(data).take_front(100));
  XXH3_128Stream second(std::move(first));
  second.update(as_bytes(data).drop_front(100));
  EXPECT_EQ(second.bytes(), 300u);
  EXPECT_EQ(second.finalize(), BLI_hash_xxh3_128(as_bytes(data)));
  XXH3_128Stream third;
  third = std::move(second);
  EXPECT_EQ(third.finalize(), BLI_hash_xxh3_128(as_bytes(data)));
}

}  // namespace blender::tests
