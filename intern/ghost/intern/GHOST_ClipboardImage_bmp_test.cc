/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The file header that hands a bitmap the decoder does not read to Blender's BMP reader
 * (OpenImageIO): its offsets lie within the bytes, and only what that reader parses fully is
 * handed over, so the size checked here bounds what it allocates.
 */

#include "testing/testing.h"

#include <cstdint>
#include <utility>
#include <vector>

#include "GHOST_ClipboardImage.hh"
#include "GHOST_ClipboardImage_test_dib.hh"

namespace ghost_clipboard_image::tests {

static uint32_t pixels_at(const uint8_t file[GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES])
{
  return uint32_t(file[10]) | uint32_t(file[11]) << 8 | uint32_t(file[12]) << 16 |
         uint32_t(file[13]) << 24;
}

TEST(GHOST_ClipboardImage, DibFileHeaderOnlyForWhatTheBmpReaderReads)
{
  uint8_t file[GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES];
  Dib indexed;
  indexed.header_bytes = DIB_INFO_HEADER;
  indexed.width = 2;
  indexed.height = 2;
  indexed.bit_count = 8;
  /* A full 256-entry palette when the header counts none, then two rows of 4 bytes. */
  std::vector<uint8_t> bytes = indexed.bytes(std::vector<uint8_t>(256 * 4 + 8, 0));
  ASSERT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  EXPECT_EQ(file[0], 'B');
  EXPECT_EQ(file[1], 'M');
  EXPECT_EQ(pixels_at(file), 14u + 40u + 1024u);
  EXPECT_EQ(uint32_t(file[2]) | uint32_t(file[3]) << 8, 14u + bytes.size());
  EXPECT_FALSE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size() - 1, file))
      << "a row short";

  indexed.colors_used = 2;
  bytes = indexed.bytes(std::vector<uint8_t>(2 * 4 + 8, 0));
  ASSERT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  EXPECT_EQ(pixels_at(file), 14u + 40u + 8u);
  indexed.colors_used = 257; /* More colours than 8 bits index. */
  bytes = indexed.bytes(std::vector<uint8_t>(4096, 0));
  EXPECT_FALSE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));

  /* 16-bit with masks after a `BITMAPINFOHEADER`; RLE without a row size. */
  Dib fields16 = indexed;
  fields16.colors_used = 0;
  fields16.bit_count = 16;
  fields16.compression = DIB_BITFIELDS;
  bytes = fields16.bytes(std::vector<uint8_t>(12 + 8, 0));
  ASSERT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  EXPECT_EQ(pixels_at(file), 14u + 40u + 12u);
  Dib rle = indexed;
  rle.colors_used = 2;
  rle.compression = DIB_RLE8;
  bytes = rle.bytes(std::vector<uint8_t>(8 + 2, 0));
  EXPECT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  bytes.resize(bytes.size() - 3); /* The palette ends past the data. */
  EXPECT_FALSE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));

  /* What the decoder reads itself, and what no reader reads. */
  for (const auto [bits, compression] : {std::pair{24, DIB_RGB},
                                         std::pair{32, DIB_RGB},
                                         std::pair{32, DIB_BITFIELDS},
                                         std::pair{0, DIB_JPEG},
                                         std::pair{0, DIB_PNG},
                                         std::pair{8, DIB_BITFIELDS}})
  {
    Dib other;
    other.bit_count = uint16_t(bits);
    other.compression = compression;
    bytes = other.bytes(std::vector<uint8_t>(4096, 0));
    EXPECT_FALSE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file))
        << bits << "-bit, compression " << compression;
  }
}

TEST(GHOST_ClipboardImage, DibFileHeaderOnlyForHeaderSizesTheBmpReaderParses)
{
  /* The BMP reader parses 40, 52, 56, 108 and 124-byte headers; with another (64, an OS/2 2.x
   * header, or 100) it would read neither sides nor depth. */
  uint8_t file[GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES];
  for (const uint32_t header_bytes : {40u, 52u, 56u, 108u, 124u, 64u, 100u, 41u}) {
    Dib indexed;
    indexed.header_bytes = header_bytes;
    indexed.width = 2;
    indexed.height = 2;
    indexed.bit_count = 8;
    indexed.colors_used = 2;
    const std::vector<uint8_t> bytes = indexed.bytes(std::vector<uint8_t>(2 * 4 + 8, 0));
    const bool parsed = header_bytes == 40 || header_bytes == 52 || header_bytes == 56 ||
                        header_bytes == 108 || header_bytes == 124;
    EXPECT_EQ(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file), parsed)
        << header_bytes << "-byte header";
  }
}

TEST(GHOST_ClipboardImage, DibFileHeaderRefusesTopDownRunLength)
{
  /* Run-length bitmaps are bottom-up only; the BMP reader would flip a top-down one. */
  uint8_t file[GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES];
  Dib rle;
  rle.header_bytes = DIB_INFO_HEADER;
  rle.width = 2;
  rle.height = 2;
  rle.bit_count = 8;
  rle.colors_used = 2;
  rle.compression = DIB_RLE8;
  std::vector<uint8_t> bytes = rle.bytes(std::vector<uint8_t>(8 + 2, 0));
  EXPECT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  rle.height = -2;
  bytes = rle.bytes(std::vector<uint8_t>(8 + 2, 0));
  EXPECT_FALSE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
}

TEST(GHOST_ClipboardImage, DibFileHeaderSkipsMasksRepeatedAfterAV5Header)
{
  /* 16-bit 5:6:5 with its masks in a V5 header and again after it (as for 32-bit): the pixels
   * start after the repeat, or the picture would be shifted by six pixels. */
  uint8_t file[GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES];
  Dib fields16;
  fields16.width = 2;
  fields16.height = 1;
  fields16.bit_count = 16;
  fields16.compression = DIB_BITFIELDS;
  fields16.masks[0] = 0xf800;
  fields16.masks[1] = 0x07e0;
  fields16.masks[2] = 0x001f;
  std::vector<uint8_t> after(12, 0);
  for (int i = 0; i < 3; i++) {
    put_u32(after, 4 * i, fields16.masks[i]);
  }
  after.insert(after.end(), {0x00, 0xf8, 0x1f, 0x00}); /* Red, blue: one 4-byte row. */
  std::vector<uint8_t> bytes = fields16.bytes(after);
  ASSERT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  EXPECT_EQ(pixels_at(file), 14u + 124u + 12u) << "after the repeated masks";
  /* With spare bytes after the pixels, the repeat is recognised by its values. */
  bytes.insert(bytes.end(), 8, 0);
  ASSERT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  EXPECT_EQ(pixels_at(file), 14u + 124u + 12u);
  /* Without a repeat the pixels follow the header. */
  bytes = fields16.bytes({0x00, 0xf8, 0x1f, 0x00});
  ASSERT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  EXPECT_EQ(pixels_at(file), 14u + 124u);
  /* Exactly three `DWORD`s to spare that are not the masks: no repeat either. */
  bytes = fields16.bytes({0x00, 0xf8, 0x1f, 0x00, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0});
  ASSERT_TRUE(GHOST_ClipboardImage_dib_file_header(bytes.data(), bytes.size(), file));
  EXPECT_EQ(pixels_at(file), 14u + 124u);
}

}  // namespace ghost_clipboard_image::tests
