/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <algorithm>
#include <cerrno>
#include <string>
#include <vector>

#include "BLI_hash_xxh3.hh"

#include "wm_checkpoint.hh"

namespace blender::tests {

/** A descriptor that accepts what a script says per call: N bytes, an EINTR, zero or an error. */
struct ScriptedDescriptor {
  /** Per call: > 0 accept up to that many bytes, 0 zero-byte write, -1 EINTR, -2 EIO. */
  std::vector<int64_t> script;
  size_t call = 0;
  std::string stored;

  int64_t write(const int descriptor, const void *data, const size_t size)
  {
    EXPECT_EQ(descriptor, 7);
    const int64_t step = call < script.size() ? script[call] : int64_t(size);
    call++;
    if (step == -1) {
      errno = EINTR;
      return -1;
    }
    if (step == -2) {
      errno = EIO;
      return -1;
    }
    const size_t accepted = std::min(size, size_t(step));
    stored.append(static_cast<const char *>(data), accepted);
    return int64_t(accepted);
  }
};

static std::string payload()
{
  std::string data(9000, '\0');
  for (size_t i = 0; i < data.size(); i++) {
    data[i] = char((i * 131) ^ (i >> 3));
  }
  return data;
}

static Span<std::byte> bytes_of(const std::string &data)
{
  return Span<std::byte>(reinterpret_cast<const std::byte *>(data.data()), int64_t(data.size()));
}

static bool run(ScriptedDescriptor &descriptor, const std::string &data, XXH3_128Stream &content)
{
  return WM_checkpoint_write_all(
      7, bytes_of(data), &content, [&](const int fd, const void *buffer, const size_t size) {
        return descriptor.write(fd, buffer, size);
      });
}

TEST(wm_checkpoint_write, ShortAndInterruptedWritesHashExactlyTheAcceptedBytes)
{
  const std::string data = payload();
  ScriptedDescriptor descriptor;
  descriptor.script = {1, -1, 4096, 3, -1, -1, 100};  /* The rest in one call afterwards. */
  XXH3_128Stream content;
  ASSERT_TRUE(run(descriptor, data, content));
  EXPECT_EQ(descriptor.stored, data);
  EXPECT_EQ(content.bytes(), uint64_t(data.size()));
  EXPECT_EQ(content.finalize(), BLI_hash_xxh3_128(bytes_of(data)));
}

TEST(wm_checkpoint_write, ZeroWriteStopsWithOnlyTheAcceptedPrefixHashed)
{
  const std::string data = payload();
  ScriptedDescriptor descriptor;
  descriptor.script = {500, 0};
  XXH3_128Stream content;
  EXPECT_FALSE(run(descriptor, data, content));
  EXPECT_EQ(descriptor.stored, data.substr(0, 500));
  EXPECT_EQ(content.bytes(), 500u);
  EXPECT_EQ(content.finalize(), BLI_hash_xxh3_128(bytes_of(data).take_front(500)));
}

TEST(wm_checkpoint_write, ErrorStopsWithOnlyTheAcceptedPrefixHashed)
{
  const std::string data = payload();
  ScriptedDescriptor descriptor;
  descriptor.script = {-1, 1234, -2};
  XXH3_128Stream content;
  EXPECT_FALSE(run(descriptor, data, content));
  EXPECT_EQ(descriptor.call, 3u);
  EXPECT_EQ(content.bytes(), 1234u);
  EXPECT_EQ(content.finalize(), BLI_hash_xxh3_128(bytes_of(data).take_front(1234)));
}

TEST(wm_checkpoint_write, ImpossibleCountIsRefused)
{
  const std::string data = payload();
  XXH3_128Stream content;
  EXPECT_FALSE(WM_checkpoint_write_all(
      7, bytes_of(data), &content, [&](const int, const void *, const size_t size) {
        return int64_t(size) + 1;
      }));
  EXPECT_EQ(content.bytes(), 0u);
}

TEST(wm_checkpoint_write, EmptyInputWritesNothing)
{
  ScriptedDescriptor descriptor;
  XXH3_128Stream content;
  EXPECT_TRUE(run(descriptor, std::string(), content));
  EXPECT_EQ(descriptor.call, 0u);
  EXPECT_EQ(content.bytes(), 0u);
}

TEST(wm_checkpoint_write, NoStreamStillWritesEverything)
{
  const std::string data = payload();
  ScriptedDescriptor descriptor;
  descriptor.script = {10, -1, 20};
  EXPECT_TRUE(WM_checkpoint_write_all(
      7, bytes_of(data), nullptr, [&](const int fd, const void *buffer, const size_t size) {
        return descriptor.write(fd, buffer, size);
      }));
  EXPECT_EQ(descriptor.stored, data);
}

}  // namespace blender::tests
