/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The byte-level clipboard picture readers (`GHOST_ClipboardImage.cc`), on every platform.
 *
 * Clipboard bytes are untrusted, so most tests are bytes no well-behaved application writes:
 * truncated pixels, a colour table that overflows 32-bit offsets, formats the readers do not
 * decode, sides that overflow. Each refusal is a null or false, never a picture made of unread
 * memory. The macOS bitmap rows are in `GHOST_ClipboardImage_bitmap_test.cc`, the bitmaps handed
 * to the BMP reader in `GHOST_ClipboardImage_bmp_test.cc`.
 */

#include "testing/testing.h"

#include <climits>
#include <cstdint>
#include <cstdlib>
#include <vector>

#include "GHOST_ClipboardImage.hh"
#include "GHOST_ClipboardImage_test_dib.hh"

namespace ghost_clipboard_image::tests {

/* -------------------------------------------------------------------- */
/** \name Device-Independent Bitmaps
 * \{ */

TEST(GHOST_ClipboardImage, DibSizeReadsTheHeaderAlone)
{
  Dib dib;
  dib.width = 6000;
  dib.height = -4000;                              /* Top-down. */
  const std::vector<uint8_t> header = dib.bytes(); /* No pixels at all. */
  int width = 0, height = 0;
  ASSERT_TRUE(GHOST_ClipboardImage_dib_size(header.data(), header.size(), &width, &height));
  EXPECT_EQ(width, 6000);
  EXPECT_EQ(height, 4000);

  /* A header shorter than a `BITMAPINFOHEADER`, or longer than the bytes. */
  EXPECT_FALSE(GHOST_ClipboardImage_dib_size(header.data(), 39, &width, &height));
  EXPECT_FALSE(GHOST_ClipboardImage_dib_size(header.data(), DIB_V5_HEADER - 1, &width, &height));
  EXPECT_FALSE(GHOST_ClipboardImage_dib_size(nullptr, 0, &width, &height));
  for (const int32_t side : {0, -1}) {
    Dib bad = dib;
    bad.width = side;
    const std::vector<uint8_t> bytes = bad.bytes();
    EXPECT_FALSE(GHOST_ClipboardImage_dib_size(bytes.data(), bytes.size(), &width, &height));
  }
  for (const int32_t side : {0, INT32_MIN}) {
    Dib bad = dib;
    bad.height = side;
    const std::vector<uint8_t> bytes = bad.bytes();
    EXPECT_FALSE(GHOST_ClipboardImage_dib_size(bytes.data(), bytes.size(), &width, &height));
  }
  EXPECT_EQ(width, 6000) << "a refusal leaves the outputs alone";
}

TEST(GHOST_ClipboardImage, Dib24BitRowsArePaddedAndStoredBottomUp)
{
  Dib dib;
  dib.width = 3;
  dib.height = 2;
  dib.bit_count = 24;
  /* Rows are 9 bytes of `RGBTRIPLE` (blue, green, red) padded to 12; bottom row first. */
  const std::vector<uint8_t> pixels = {
      1,  2,  3,  4,  5,  6,  7,  8,  9,  0xEE, 0xEE, 0xEE, /* Bottom row. */
      11, 12, 13, 14, 15, 16, 17, 18, 19, 0xEE, 0xEE, 0xEE, /* Top row. */
  };
  int width = 0, height = 0;
  uint8_t *rgba = decode(dib.bytes(pixels), width, height);
  ASSERT_NE(rgba, nullptr);
  EXPECT_EQ(width, 3);
  EXPECT_EQ(height, 2);
  const uint8_t expected[] = {3,  2,  1,  255, 6,  5,  4,  255, 9,  8,  7,  255,
                              13, 12, 11, 255, 16, 15, 14, 255, 19, 18, 17, 255};
  for (int i = 0; i < 24; i++) {
    EXPECT_EQ(rgba[i], expected[i]) << "byte " << i;
  }
  free(rgba);
}

TEST(GHOST_ClipboardImage, DibTopDownRowsAreFlipped)
{
  Dib dib;
  dib.width = 1;
  dib.height = -2; /* Top row first in memory. */
  const std::vector<uint8_t> pixels = {/* Top: */ 10, 20, 30, 0, /* Bottom: */ 40, 50, 60, 0};
  int width = 0, height = 0;
  uint8_t *rgba = decode(dib.bytes(pixels), width, height);
  ASSERT_NE(rgba, nullptr) << "a top-down bitmap is a picture, not an error";
  EXPECT_EQ(height, 2);
  const uint8_t expected[] = {60, 50, 40, 255, 30, 20, 10, 255}; /* Bottom row first. */
  for (int i = 0; i < 8; i++) {
    EXPECT_EQ(rgba[i], expected[i]) << "byte " << i;
  }
  free(rgba);
}

TEST(GHOST_ClipboardImage, Dib32BitAlphaIsReadAsChromiumReadsIt)
{
  /* Windows stores premultiplied alpha. Three ways a 32-bit bitmap carries the fourth byte: plain
   * `BI_RGB` (the reserved byte), `BI_BITFIELDS` with an alpha mask, and `BI_BITFIELDS` with
   * colour masks only (the top byte, unused by colour). Each reads the same. */
  Dib rgb;
  rgb.width = 2;
  Dib alpha_mask = rgb;
  alpha_mask.compression = DIB_BITFIELDS;
  alpha_mask.masks[0] = 0x00ff0000;
  alpha_mask.masks[1] = 0x0000ff00;
  alpha_mask.masks[2] = 0x000000ff;
  alpha_mask.masks[3] = 0xff000000;
  Dib colour_masks = alpha_mask;
  colour_masks.masks[3] = 0;
  auto check = [](const Dib &dib,
                  const std::vector<uint8_t> &bgra,
                  const std::vector<uint8_t> &expected,
                  const char *what) {
    int width = 0, height = 0;
    uint8_t *rgba = decode(dib.bytes(bgra), width, height);
    ASSERT_NE(rgba, nullptr) << what;
    for (int i = 0; i < 8; i++) {
      EXPECT_EQ(rgba[i], expected[i])
          << what << ", compression " << dib.compression << ", byte " << i;
    }
    free(rgba);
  };
  for (const Dib &dib : {rgb, alpha_mask, colour_masks}) {
    /* Premultiplied (no colour above its alpha): divided out; a transparent pixel stays so. */
    check(dib, {0, 32, 64, 128, 0, 0, 0, 0}, {128, 64, 0, 128, 0, 0, 0, 0}, "premultiplied");
    /* One colour above its alpha: the byte is not alpha (GDI leaves 0), the picture opaque. */
    check(dib, {10, 20, 30, 0, 40, 50, 60, 0}, {30, 20, 10, 255, 60, 50, 40, 255}, "padding");
    check(dib, {0, 32, 64, 128, 10, 10, 200, 100}, {64, 32, 0, 255, 200, 10, 10, 255}, "garbage");
    /* Alpha 0 everywhere, even on black: opaque, never an invisible picture (where Chromium
     * would keep an all-black bitmap transparent). */
    check(dib, {0, 0, 0, 0, 0, 0, 0, 0}, {0, 0, 0, 255, 0, 0, 0, 255}, "all zero");
    check(dib, {1, 2, 3, 255, 4, 5, 6, 255}, {3, 2, 1, 255, 6, 5, 4, 255}, "opaque");
  }
}

TEST(GHOST_ClipboardImage, DibBitfieldsMasksAreReadWhereverTheyAre)
{
  /* Masks in a V5 header: blue, green, red, alpha from the low byte up (a common layout). */
  const uint32_t masks[4] = {0x00ff0000, 0x0000ff00, 0x000000ff, 0xff000000};
  const std::vector<uint8_t> pixel = {10, 20, 30, 255}; /* Blue 10, green 20, red 30. */
  const uint8_t expected[4] = {30, 20, 10, 255};

  Dib v5;
  v5.compression = DIB_BITFIELDS;
  for (int i = 0; i < 4; i++) {
    v5.masks[i] = masks[i];
  }
  int width = 0, height = 0;
  uint8_t *rgba = decode(v5.bytes(pixel), width, height);
  ASSERT_NE(rgba, nullptr);
  for (int i = 0; i < 4; i++) {
    EXPECT_EQ(rgba[i], expected[i]) << "V5 masks, channel " << i;
  }
  free(rgba);

  /* The same masks written again after the V5 header (Windows Snipping Tool). */
  std::vector<uint8_t> repeated(12, 0);
  for (int i = 0; i < 3; i++) {
    put_u32(repeated, 4 * i, masks[i]);
  }
  repeated.insert(repeated.end(), pixel.begin(), pixel.end());
  rgba = decode(v5.bytes(repeated), width, height);
  ASSERT_NE(rgba, nullptr);
  for (int i = 0; i < 4; i++) {
    EXPECT_EQ(rgba[i], expected[i]) << "repeated masks skipped, channel " << i;
  }
  free(rgba);

  /* A `BITMAPINFOHEADER` has no mask fields: its three masks follow it. */
  Dib info;
  info.header_bytes = DIB_INFO_HEADER;
  info.compression = DIB_BITFIELDS;
  std::vector<uint8_t> after(12, 0);
  for (int i = 0; i < 3; i++) {
    put_u32(after, 4 * i, masks[i]);
  }
  after.insert(after.end(), pixel.begin(), pixel.end());
  rgba = decode(info.bytes(after), width, height);
  ASSERT_NE(rgba, nullptr);
  for (int i = 0; i < 3; i++) {
    EXPECT_EQ(rgba[i], expected[i]) << "masks after the header, channel " << i;
  }
  free(rgba);
}

TEST(GHOST_ClipboardImage, DibMasksWiderOrNarrowerThanAByteAreScaled)
{
  /* 10-bit colour and 2-bit alpha (`DXGI_FORMAT_R10G10B10A2`): red is half, alpha full. */
  Dib dib;
  dib.compression = DIB_BITFIELDS;
  dib.masks[0] = 0x000003ff;
  dib.masks[1] = 0x000ffc00;
  dib.masks[2] = 0x3ff00000;
  dib.masks[3] = 0xc0000000;
  std::vector<uint8_t> pixel(4, 0);
  put_u32(pixel, 0, 0x200u | (0x3ffu << 10) | (0u << 20) | (3u << 30));
  int width = 0, height = 0;
  uint8_t *rgba = decode(dib.bytes(pixel), width, height);
  ASSERT_NE(rgba, nullptr);
  EXPECT_EQ(rgba[0], 128) << "the top bits count, not the low byte";
  EXPECT_EQ(rgba[1], 255);
  EXPECT_EQ(rgba[2], 0);
  EXPECT_EQ(rgba[3], 255);
  free(rgba);
}

TEST(GHOST_ClipboardImage, DibShorterThanItsHeaderSaysIsRefused)
{
  Dib dib;
  dib.width = 4;
  dib.height = 4;
  std::vector<uint8_t> pixels(4 * 4 * 4, 0x55);
  int width = 0, height = 0;
  uint8_t *rgba = decode(dib.bytes(pixels), width, height);
  ASSERT_NE(rgba, nullptr);
  free(rgba);

  pixels.pop_back(); /* One byte short: the last pixel would be read past the data. */
  width = height = -1;
  EXPECT_EQ(decode(dib.bytes(pixels), width, height), nullptr);
  EXPECT_EQ(width, -1) << "nothing reported for a refused picture";
  EXPECT_EQ(height, -1);

  /* 24-bit rows are padded: the padding of the last row counts too. */
  Dib padded;
  padded.width = 1;
  padded.height = 2;
  padded.bit_count = 24;
  EXPECT_EQ(decode(padded.bytes(std::vector<uint8_t>(4 + 3, 0)), width, height), nullptr);
  rgba = decode(padded.bytes(std::vector<uint8_t>(8, 0)), width, height);
  EXPECT_NE(rgba, nullptr);
  free(rgba);

  /* Masks after a `BITMAPINFOHEADER` that end early. */
  Dib info;
  info.header_bytes = DIB_INFO_HEADER;
  info.compression = DIB_BITFIELDS;
  EXPECT_EQ(decode(info.bytes(std::vector<uint8_t>(8, 0)), width, height), nullptr);
}

TEST(GHOST_ClipboardImage, DibColourTableThatOverflowsAnOffsetIsRefused)
{
  /* 2^30 entries of 4 bytes: in 32-bit arithmetic the offset wraps back to the header's end
   * and the "pixels" would be read from the wrong place. */
  Dib dib;
  dib.colors_used = 0x40000000;
  int width = 0, height = 0;
  EXPECT_EQ(decode(dib.bytes({1, 2, 3, 4}), width, height), nullptr);
  dib.colors_used = 0xffffffff;
  EXPECT_EQ(decode(dib.bytes({1, 2, 3, 4}), width, height), nullptr);

  /* A colour table that is really there is skipped. */
  dib.colors_used = 2;
  uint8_t *rgba = decode(dib.bytes({0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 3, 255}), width, height);
  ASSERT_NE(rgba, nullptr);
  EXPECT_EQ(rgba[0], 3);
  EXPECT_EQ(rgba[2], 1);
  free(rgba);
}

TEST(GHOST_ClipboardImage, DibFormatsItCannotReadAreRefusedWithNothingAllocated)
{
  struct Format {
    uint16_t bit_count;
    uint32_t compression;
  };
  /* clang-format off */
  const Format formats[] = {{1, DIB_RGB}, {4, DIB_RGB}, {8, DIB_RGB}, {16, DIB_RGB}, {48, DIB_RGB},
                            {64, DIB_RGB}, {16, DIB_BITFIELDS}, {24, DIB_BITFIELDS}, {8, DIB_RLE8},
                            {0, DIB_JPEG}, {0, DIB_PNG}, {32, DIB_PNG}};
  /* clang-format on */
  for (const Format &format : formats) {
    Dib dib;
    dib.width = 2;
    dib.height = 2;
    dib.bit_count = format.bit_count;
    dib.compression = format.compression;
    /* Plenty of bytes: the refusal is the format's, not the size's. */
    int width = -1, height = -1;
    EXPECT_EQ(decode(dib.bytes(std::vector<uint8_t>(1024, 0)), width, height), nullptr)
        << format.bit_count << "-bit, compression " << format.compression;
    EXPECT_EQ(width, -1);
  }
}

TEST(GHOST_ClipboardImage, DibSidesPastTheFormatAreRefusedBeforeAllocating)
{
  int width = 0, height = 0;
  Dib huge;
  huge.width = INT32_MAX;
  huge.height = INT32_MAX;
  EXPECT_EQ(decode(huge.bytes({1, 2, 3, 4}), width, height), nullptr);
  /* 70000 x 70000 at 24 bits is over the 4 GiB a DIB can hold. */
  huge.width = 70000;
  huge.height = -70000;
  huge.bit_count = 24;
  EXPECT_EQ(decode(huge.bytes({1, 2, 3, 4}), width, height), nullptr);
  /* Within the format, but not the bytes. */
  huge.width = 30000;
  huge.height = 30000;
  huge.bit_count = 32;
  EXPECT_EQ(decode(huge.bytes({1, 2, 3, 4}), width, height), nullptr);
}

/** \} */

TEST(GHOST_ClipboardImage, DibRepeatedMasksAreSkippedWithSpareBytesAfter)
{
  /* Masks in the V5 header, repeated after it, then the pixels and 4 spare bytes (a memory block
   * rounded up): the repeat is not read as pixels. */
  Dib dib;
  dib.width = 4;
  dib.compression = DIB_BITFIELDS;
  dib.masks[0] = 0x00ff0000;
  dib.masks[1] = 0x0000ff00;
  dib.masks[2] = 0x000000ff;
  dib.masks[3] = 0xff000000;
  std::vector<uint8_t> after(12, 0);
  for (int i = 0; i < 3; i++) {
    put_u32(after, 4 * i, dib.masks[i]);
  }
  for (int i = 0; i < 4; i++) {
    after.insert(after.end(), {1, 2, 3, 255});
  }
  after.insert(after.end(), 4, 0);
  int width = 0, height = 0;
  uint8_t *rgba = decode(dib.bytes(after), width, height);
  ASSERT_NE(rgba, nullptr);
  for (int i = 0; i < 4; i++) {
    EXPECT_EQ(rgba[i * 4 + 0], 3) << "pixel " << i;
    EXPECT_EQ(rgba[i * 4 + 2], 1) << "pixel " << i;
    EXPECT_EQ(rgba[i * 4 + 3], 255) << "pixel " << i;
  }
  free(rgba);

  /* Spare bytes after pixels that do not repeat the masks: the pixels start right away. */
  std::vector<uint8_t> plain;
  for (int i = 0; i < 4; i++) {
    plain.insert(plain.end(), {7, 8, 9, 255});
  }
  plain.insert(plain.end(), 16, 0);
  rgba = decode(dib.bytes(plain), width, height);
  ASSERT_NE(rgba, nullptr);
  EXPECT_EQ(rgba[0], 9);
  free(rgba);

  /* Exactly three `DWORD`s to spare that are not the masks: still no repeat. */
  std::vector<uint8_t> first_then_spare;
  first_then_spare.insert(first_then_spare.end(), {11, 12, 13, 255});
  for (int i = 1; i < 4; i++) {
    first_then_spare.insert(first_then_spare.end(), {7, 8, 9, 255});
  }
  first_then_spare.insert(first_then_spare.end(), 12, 0);
  rgba = decode(dib.bytes(first_then_spare), width, height);
  ASSERT_NE(rgba, nullptr);
  EXPECT_EQ(rgba[0], 13) << "the first pixel is read where it is";
  EXPECT_EQ(rgba[2], 11);
  free(rgba);
}

TEST(GHOST_ClipboardImage, WindowsReadsPngBeforeAnyBitmap)
{
  /* Clipboard format numbers: `CF_BITMAP`, `CF_DIB`, `CF_DIBV5` and a registered "PNG". */
  const uint32_t bitmap = 2, dib = 8, dibv5 = 17, png = 0xc1a0;
  auto order = [&](const std::vector<uint32_t> &formats) {
    uint32_t chosen[2] = {0, 0};
    const size_t count = GHOST_ClipboardImage_windows_formats(
        formats.data(), formats.size(), png, dibv5, chosen);
    return std::vector<uint32_t>(chosen, chosen + count);
  };
  /* Blender's own copy (`putClipboardImage`): its straight-alpha `CF_DIBV5` first, then "PNG",
   * then what Windows makes from the bitmap. The PNG is read, whose alpha is unambiguous; the
   * bitmap only if the PNG's data cannot be had or decoded (as Chromium falls back). */
  EXPECT_EQ(order({dibv5, png, bitmap, dib}), (std::vector<uint32_t>{png, dibv5}));
  EXPECT_EQ(order({png}), (std::vector<uint32_t>{png}));
  EXPECT_EQ(order({bitmap, dib, dibv5}), (std::vector<uint32_t>{dibv5})) << "no PNG";
  EXPECT_EQ(order({bitmap}), (std::vector<uint32_t>{}));
  EXPECT_EQ(order({}), (std::vector<uint32_t>{}));
}

TEST(GHOST_ClipboardImage, BlendersOwnBitmapAloneReadsByThePremultipliedRule)
{
  /* `putClipboardImageDibV5`'s exact layout: a V5 header, `BI_BITFIELDS`, red, green, blue and
   * alpha masks 0xff, 0xff00, 0xff0000, 0xff000000, straight RGBA bytes. Opaque pictures read
   * exactly. A bitmap cannot say its alpha is straight, so a translucent one alone is read by the
   * premultiplied rule (40 at alpha 128 reads as 80): why "PNG" is read first whenever it is there
   * (#GHOST_ClipboardImage_windows_formats), as Blender writes it beside the bitmap. */
  Dib blender;
  blender.width = 2;
  blender.compression = DIB_BITFIELDS;
  blender.masks[0] = 0x000000ff;
  blender.masks[1] = 0x0000ff00;
  blender.masks[2] = 0x00ff0000;
  blender.masks[3] = 0xff000000;
  int width = 0, height = 0;
  uint8_t *rgba = decode(blender.bytes({10, 20, 30, 255, 200, 100, 50, 255}), width, height);
  ASSERT_NE(rgba, nullptr);
  const uint8_t opaque[8] = {10, 20, 30, 255, 200, 100, 50, 255};
  for (int i = 0; i < 8; i++) {
    EXPECT_EQ(rgba[i], opaque[i]) << "byte " << i;
  }
  free(rgba);
  rgba = decode(blender.bytes({40, 20, 10, 128, 0, 0, 0, 0}), width, height);
  ASSERT_NE(rgba, nullptr);
  EXPECT_EQ(rgba[0], 80);
  EXPECT_EQ(rgba[3], 128);
  free(rgba);
}

/** \} */

}  // namespace ghost_clipboard_image::tests
