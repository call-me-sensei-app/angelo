/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "../intern/writefile_compression.hh"
#include "testing/testing.h"

namespace blender::tests {

class TestWriteWrap : public WriteWrap {
 public:
  std::string bytes, path;
  int opens = 0, closes = 0, writes = 0;
  bool fail_open = false, fail_write = false, fail_close = false;

  bool open(const char *filepath) override
  {
    opens++;
    path = filepath;
    bytes.clear();
    return !fail_open;
  }
  bool write(const void *data, size_t size) override
  {
    writes++;
    if (fail_write) {
      return false;
    }
    if (size) {
      bytes.append(static_cast<const char *>(data), size);
    }
    return true;
  }
  bool close() override
  {
    closes++;
    return !fail_close;
  }
};

TEST(StageCompression, SizeBoundariesPreserveEveryByte)
{
  for (const int size : {0, 1, 7, 8, 9, 31}) {
    TestWriteWrap raw, compressed;
    MinimumSizeWriteWrap writer(raw, compressed, 8);
    ASSERT_TRUE(writer.open("temporary.blend@"));
    std::string expected;
    for (int i = 0; i < size; i++) {
      const char byte = char(i);
      expected += byte;
      ASSERT_TRUE(writer.write(&byte, 1));
    }
    ASSERT_TRUE(writer.close());
    EXPECT_EQ(size > 8 ? compressed.bytes : raw.bytes, expected);
    EXPECT_EQ(compressed.opens, int(size > 8));
    EXPECT_EQ(raw.closes, 1);
    EXPECT_EQ(compressed.closes, int(size > 8));
  }
}

TEST(StageCompression, LargeFirstWriteDoesNotNeedPrefixAllocation)
{
  TestWriteWrap raw, compressed;
  MinimumSizeWriteWrap writer(raw, compressed, 8);
  ASSERT_TRUE(writer.open("temporary.blend@"));
  const std::string input(1024 * 1024 + 13, 'a');
  ASSERT_TRUE(writer.write(input.data(), input.size()));
  ASSERT_TRUE(writer.close());
  EXPECT_EQ(compressed.bytes, input);
  EXPECT_EQ(raw.writes, 0);
}

TEST(StageCompression, BufferedPrefixIsReplayedInBoundedChunks)
{
  TestWriteWrap raw, compressed;
  const std::string input(2 * 1024 * 1024 + 5, 'p');
  MinimumSizeWriteWrap writer(raw, compressed, input.size());
  ASSERT_TRUE(writer.open("temporary.blend@"));
  ASSERT_TRUE(writer.write(input.data(), input.size()));
  ASSERT_TRUE(writer.write("end", 3));
  ASSERT_TRUE(writer.close());
  EXPECT_EQ(compressed.bytes, input + "end");
  EXPECT_EQ(compressed.writes, 4);
}

TEST(StageCompression, RawWriteFailureRetriesSameFileCompressed)
{
  TestWriteWrap raw, compressed;
  raw.fail_write = true;
  MinimumSizeWriteWrap writer(raw, compressed, 8);
  ASSERT_TRUE(writer.open("temporary.blend@"));
  ASSERT_TRUE(writer.write("12345678", 8));
  ASSERT_TRUE(writer.close());
  EXPECT_EQ(compressed.bytes, "12345678");
  EXPECT_EQ(compressed.path, raw.path);
  EXPECT_EQ(raw.closes, 1);
  EXPECT_EQ(compressed.closes, 1);
}

TEST(StageCompression, ErrorsPropagateAndOpenedStreamsAlwaysClose)
{
  for (int scenario = 0; scenario < 5; scenario++) {
    TestWriteWrap raw, compressed;
    MinimumSizeWriteWrap writer(raw, compressed, 8);
    ASSERT_TRUE(writer.open("temporary.blend@"));
    raw.fail_write = scenario != 4;
    raw.fail_close = scenario == 0 || scenario == 4;
    compressed.fail_open = scenario == 1;
    compressed.fail_write = scenario == 2;
    compressed.fail_close = scenario == 3;
    ASSERT_TRUE(writer.write("prefix", 6));
    EXPECT_FALSE(writer.close());
    EXPECT_EQ(raw.closes, 1);
    EXPECT_EQ(compressed.closes, int(scenario == 2 || scenario == 3));
  }
}

TEST(StageCompression, FailedTransitionRejectsLaterWrites)
{
  TestWriteWrap raw, compressed;
  MinimumSizeWriteWrap writer(raw, compressed, 8);
  ASSERT_TRUE(writer.open("temporary.blend@"));
  ASSERT_TRUE(writer.write("prefix", 6));
  compressed.fail_open = true;
  EXPECT_FALSE(writer.write("suffix", 6));
  EXPECT_FALSE(writer.write("x", 1));
  EXPECT_FALSE(writer.close());
  EXPECT_EQ(raw.closes, 1);
  EXPECT_EQ(compressed.closes, 0);
}

TEST(StageCompression, FailedInitialOpenDoesNotOpenCompressor)
{
  TestWriteWrap raw, compressed;
  raw.fail_open = true;
  MinimumSizeWriteWrap writer(raw, compressed, 8);
  EXPECT_FALSE(writer.open("temporary.blend@"));
  EXPECT_EQ(compressed.opens, 0);
}

}  // namespace blender::tests
