/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Sizes and the bitmap row reader (`GHOST_ClipboardImage.cc`), on every platform: 8-bit RGB(A)
 * rows are read at their own stride, alpha position and premultiplication, and the tight
 * premultiplied RGBA layout every macOS bitmap is drawn into is converted in place.
 */

#include "testing/testing.h"

#include <climits>
#include <cstdint>
#include <vector>

#include "GHOST_ClipboardImage.hh"

namespace ghost_clipboard_image::tests {

/* -------------------------------------------------------------------- */
/** \name Sizes
 * \{ */

TEST(GHOST_ClipboardImage, RowOffsetsNeverWrap)
{
  /* Row 20000 of a 40000 px wide RGBA bitmap starts 3.2 GB in: past `INT_MAX`. */
  EXPECT_EQ(GHOST_ClipboardImage_row_offset(20000, 160000), size_t(3200000000u));
  EXPECT_EQ(GHOST_ClipboardImage_row_offset(0, 160000), size_t(0));
  EXPECT_EQ(GHOST_ClipboardImage_row_offset(-1, 4), SIZE_MAX);
  EXPECT_EQ(GHOST_ClipboardImage_row_offset(4, -1), SIZE_MAX);
  if (sizeof(size_t) == 8) {
    EXPECT_EQ(GHOST_ClipboardImage_row_offset(INT64_MAX, 4), SIZE_MAX);
  }
}

TEST(GHOST_ClipboardImage, RgbaSizeOnlyForValidSides)
{
  EXPECT_EQ(GHOST_ClipboardImage_rgba_size(3, 2), size_t(24));
  EXPECT_EQ(GHOST_ClipboardImage_rgba_size(0, 2), size_t(0));
  EXPECT_EQ(GHOST_ClipboardImage_rgba_size(2, -1), size_t(0));
  EXPECT_EQ(GHOST_ClipboardImage_rgba_size(int64_t(INT_MAX) + 1, 1), size_t(0));
  if (sizeof(size_t) == 8) {
    EXPECT_EQ(GHOST_ClipboardImage_rgba_size(40000, 20000), size_t(3200000000u));
    EXPECT_NE(GHOST_ClipboardImage_rgba_size(INT_MAX, INT_MAX), size_t(0));
  }
  else {
    EXPECT_EQ(GHOST_ClipboardImage_rgba_size(40000, 20000), size_t(0));
  }
}

TEST(GHOST_ClipboardImage, PngSizeComesFromItsFirstBytes)
{
  /* Signature, `IHDR` length 13 and type, width 6000, height 4000 (big-endian). */
  std::vector<uint8_t> png = {137, 80,  78,  71,  13, 10, 26, 10,  0, 0, 0,  13,
                              'I', 'H', 'D', 'R', 0,  0,  23, 112, 0, 0, 15, 160};
  ASSERT_EQ(png.size(), GHOST_CLIPBOARD_PNG_HEADER_BYTES);
  int width = 0, height = 0;
  ASSERT_TRUE(GHOST_ClipboardImage_png_size(png.data(), png.size(), &width, &height));
  EXPECT_EQ(width, 6000);
  EXPECT_EQ(height, 4000);

  width = height = -1;
  EXPECT_FALSE(GHOST_ClipboardImage_png_size(png.data(), png.size() - 1, &width, &height));
  EXPECT_FALSE(GHOST_ClipboardImage_png_size(nullptr, 0, &width, &height));
  std::vector<uint8_t> bad = png;
  bad[1] = 'X'; /* Not a PNG. */
  EXPECT_FALSE(GHOST_ClipboardImage_png_size(bad.data(), bad.size(), &width, &height));
  bad = png;
  bad[12] = 'X'; /* Not `IHDR` first. */
  EXPECT_FALSE(GHOST_ClipboardImage_png_size(bad.data(), bad.size(), &width, &height));
  bad = png;
  bad[16] = bad[17] = bad[18] = bad[19] = 0; /* No width. */
  EXPECT_FALSE(GHOST_ClipboardImage_png_size(bad.data(), bad.size(), &width, &height));
  bad = png;
  bad[16] = 0x80; /* Past `INT_MAX`. */
  EXPECT_FALSE(GHOST_ClipboardImage_png_size(bad.data(), bad.size(), &width, &height));
  EXPECT_EQ(width, -1);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Bitmap Rows
 * \{ */

static GHOST_ClipboardBitmapLayout rgba_layout(const int64_t width, const int64_t height)
{
  GHOST_ClipboardBitmapLayout layout;
  layout.width = width;
  layout.height = height;
  layout.bytes_per_row = width * 4;
  layout.bits_per_pixel = 32;
  layout.bits_per_sample = 8;
  layout.samples_per_pixel = 4;
  layout.has_alpha = true;
  layout.rgb = true;
  return layout;
}

TEST(GHOST_ClipboardImage, BitmapRowsAreReadAtTheirOwnStrideAndFlipped)
{
  /* 2 x 2 pixels with 8 bytes of padding after each row (`bytesPerRow` 16, not 8). */
  GHOST_ClipboardBitmapLayout layout = rgba_layout(2, 2);
  layout.bytes_per_row = 16;
  const std::vector<uint8_t> pixels = {
      1, 2, 3, 255, 4,  5,  6,  255, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, /* Top. */
      7, 8, 9, 255, 10, 11, 12, 255, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, 0xEE, /* Bottom. */
  };
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_readable(layout));
  std::vector<uint8_t> rgba(GHOST_ClipboardImage_rgba_size(2, 2), 0);
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(layout, pixels.data(), rgba.data()));
  const uint8_t expected[] = {7, 8, 9, 255, 10, 11, 12, 255, 1, 2, 3, 255, 4, 5, 6, 255};
  for (int i = 0; i < 16; i++) {
    EXPECT_EQ(rgba[i], expected[i]) << "byte " << i;
  }
}

TEST(GHOST_ClipboardImage, BitmapWithoutAlphaIsOpaqueWhereverItsSpareByteIs)
{
  /* RGB in 32 bits with the spare byte last, then first; and tight 24-bit RGB. */
  GHOST_ClipboardBitmapLayout layout = rgba_layout(1, 1);
  layout.samples_per_pixel = 3;
  layout.has_alpha = false;
  std::vector<uint8_t> rgba(4, 0);

  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(
      layout, std::vector<uint8_t>{1, 2, 3, 0}.data(), rgba.data()));
  EXPECT_EQ(rgba, (std::vector<uint8_t>{1, 2, 3, 255}));

  layout.alpha_first = true;
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(
      layout, std::vector<uint8_t>{0, 1, 2, 3}.data(), rgba.data()));
  EXPECT_EQ(rgba, (std::vector<uint8_t>{1, 2, 3, 255}));

  layout.alpha_first = false;
  layout.bits_per_pixel = 24;
  layout.bytes_per_row = 4; /* Three bytes and a byte of padding. */
  layout.width = 1;
  layout.height = 2;
  std::vector<uint8_t> two(8, 0);
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(
      layout, std::vector<uint8_t>{1, 2, 3, 0xEE, 4, 5, 6, 0xEE}.data(), two.data()));
  EXPECT_EQ(two, (std::vector<uint8_t>{4, 5, 6, 255, 1, 2, 3, 255}));
}

TEST(GHOST_ClipboardImage, BitmapAlphaFirstAndPremultipliedAreReadAsStraightRgba)
{
  GHOST_ClipboardBitmapLayout layout = rgba_layout(1, 1);
  std::vector<uint8_t> rgba(4, 0);
  layout.alpha_first = true;
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(
      layout, std::vector<uint8_t>{200, 1, 2, 3}.data(), rgba.data()));
  EXPECT_EQ(rgba, (std::vector<uint8_t>{1, 2, 3, 200}));

  /* Half-transparent premultiplied (128, 64, 0) is straight (255, 128, 0). */
  layout.alpha_first = false;
  layout.alpha_premultiplied = true;
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(
      layout, std::vector<uint8_t>{128, 64, 0, 128}.data(), rgba.data()));
  EXPECT_EQ(rgba, (std::vector<uint8_t>{255, 128, 0, 128}));
  /* Opaque and fully transparent pixels are unchanged by the division. */
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(
      layout, std::vector<uint8_t>{9, 8, 7, 255}.data(), rgba.data()));
  EXPECT_EQ(rgba, (std::vector<uint8_t>{9, 8, 7, 255}));
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(
      layout, std::vector<uint8_t>{0, 0, 0, 0}.data(), rgba.data()));
  EXPECT_EQ(rgba, (std::vector<uint8_t>{0, 0, 0, 0}));
}

TEST(GHOST_ClipboardImage, BitmapLayoutsTheReaderCannotReadAreRefused)
{
  /* Never misread: macOS draws every bitmap into #GHOST_ClipboardImage_draw_layout first. */
  const GHOST_ClipboardBitmapLayout good = rgba_layout(4, 4);
  ASSERT_TRUE(GHOST_ClipboardImage_bitmap_readable(good));

  auto refused = [&](auto change, const char *what) {
    GHOST_ClipboardBitmapLayout layout = good;
    change(layout);
    EXPECT_FALSE(GHOST_ClipboardImage_bitmap_readable(layout)) << what;
    std::vector<uint8_t> rgba(GHOST_ClipboardImage_rgba_size(4, 4), 0x77);
    std::vector<uint8_t> pixels(4096, 0);
    EXPECT_FALSE(GHOST_ClipboardImage_bitmap_to_rgba(layout, pixels.data(), rgba.data())) << what;
    EXPECT_EQ(rgba[0], 0x77) << what << ": nothing written";
  };
  refused([](auto &l) { l.bits_per_sample = 16, l.bits_per_pixel = 64; }, "16-bit samples");
  refused([](auto &l) { l.float_samples = true; }, "float samples");
  refused([](auto &l) { l.planar = true; }, "planar");
  refused([](auto &l) { l.rgb = false; }, "CMYK: four samples, no alpha, not RGB");
  refused([](auto &l) { l.has_alpha = false; }, "four samples without alpha");
  refused([](auto &l) { l.samples_per_pixel = 2; }, "grey and alpha");
  refused([](auto &l) { l.bits_per_pixel = 24; }, "four samples in 24 bits");
  refused([](auto &l) { l.bytes_per_row = 15; }, "rows shorter than their pixels");
  refused([](auto &l) { l.width = 0; }, "no width");
  refused([](auto &l) { l.height = int64_t(INT_MAX) + 1; }, "a side past INT_MAX");
  refused([](auto &l) { l.bytes_per_row = INT64_MAX; }, "rows whose bytes overflow");
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Converted Pictures (every macOS bitmap)
 * \{ */

TEST(GHOST_ClipboardImage, DrawLayoutIsTightRgbaOrNothing)
{
  /* What every macOS picture is converted into before it is read: straight alpha from vImage,
   * premultiplied from the Core Graphics fallback. */
  GHOST_ClipboardBitmapLayout straight;
  ASSERT_TRUE(GHOST_ClipboardImage_draw_layout(3, 2, false, straight));
  EXPECT_FALSE(straight.alpha_premultiplied);
  GHOST_ClipboardBitmapLayout layout;
  ASSERT_TRUE(GHOST_ClipboardImage_draw_layout(3, 2, true, layout));
  EXPECT_EQ(layout.width, 3);
  EXPECT_EQ(layout.height, 2);
  EXPECT_EQ(layout.bytes_per_row, 12) << "explicit and tight";
  EXPECT_EQ(layout.bits_per_pixel, 32);
  EXPECT_EQ(layout.bits_per_sample, 8);
  EXPECT_EQ(layout.samples_per_pixel, 4);
  EXPECT_TRUE(layout.has_alpha && layout.alpha_premultiplied && !layout.alpha_first);
  EXPECT_TRUE(layout.rgb && layout.top_row_first && !layout.planar && !layout.float_samples);
  EXPECT_TRUE(GHOST_ClipboardImage_bitmap_readable(layout));
  EXPECT_EQ(GHOST_ClipboardImage_row_offset(layout.height, layout.bytes_per_row),
            GHOST_ClipboardImage_rgba_size(3, 2));

  /* Refused before anything is allocated, leaving the layout alone. */
  GHOST_ClipboardBitmapLayout untouched = rgba_layout(9, 9);
  for (const int64_t side : {int64_t(0), int64_t(-3), int64_t(INT_MAX) + 1}) {
    EXPECT_FALSE(GHOST_ClipboardImage_draw_layout(side, 2, false, untouched)) << side;
    EXPECT_FALSE(GHOST_ClipboardImage_draw_layout(2, side, true, untouched)) << side;
  }
  EXPECT_EQ(untouched.width, 9);
}

TEST(GHOST_ClipboardImage, DrawingIsConvertedInPlace)
{
  /* Top row first, in the buffer that is then handed over: flipped (and premultiplied colour
   * divided by alpha) without a second buffer, for an even and an odd number of rows. */
  for (const int64_t height : {int64_t(2), int64_t(3)}) {
    GHOST_ClipboardBitmapLayout straight;
    ASSERT_TRUE(GHOST_ClipboardImage_draw_layout(2, height, false, straight));
    std::vector<uint8_t> rows;
    for (int64_t row = 0; row < height; row++) {
      rows.insert(rows.end(), {uint8_t(row), 1, 2, 10, uint8_t(row), 3, 4, 10});
    }
    ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(straight, rows.data(), rows.data()));
    for (int64_t y = 0; y < height; y++) {
      EXPECT_EQ(rows[y * 8], uint8_t(height - 1 - y)) << "flipped, row " << y;
      EXPECT_EQ(rows[y * 8 + 1], 1) << "straight colour is not divided";
      EXPECT_EQ(rows[y * 8 + 3], 10);
    }

    GHOST_ClipboardBitmapLayout layout;
    ASSERT_TRUE(GHOST_ClipboardImage_draw_layout(2, height, true, layout));
    std::vector<uint8_t> pixels;
    for (int64_t row = 0; row < height; row++) {
      const uint8_t v = uint8_t(10 * (row + 1)); /* Row 0 (the top) is 10, row 1 is 20... */
      pixels.insert(pixels.end(), {v, v, 0, 255, uint8_t(v / 2), 0, 0, 128});
    }
    ASSERT_TRUE(GHOST_ClipboardImage_bitmap_to_rgba(layout, pixels.data(), pixels.data()));
    for (int64_t y = 0; y < height; y++) {
      const uint8_t v = uint8_t(10 * (height - y)); /* Bottom first. */
      const uint8_t *row = pixels.data() + y * 8;
      EXPECT_EQ(row[0], v) << "row " << y;
      EXPECT_EQ(row[1], v);
      EXPECT_EQ(row[3], 255);
      EXPECT_EQ(row[4], uint8_t((v / 2 * 255 + 64) / 128)) << "divided by alpha, row " << y;
      EXPECT_EQ(row[7], 128);
    }
  }
}

TEST(GHOST_ClipboardImage, InPlaceNeedsTightFourBytePixels)
{
  /* Padded rows or three-byte pixels would be overwritten before they are read. */
  GHOST_ClipboardBitmapLayout padded = rgba_layout(1, 2);
  padded.bytes_per_row = 8;
  std::vector<uint8_t> pixels(16, 7);
  EXPECT_FALSE(GHOST_ClipboardImage_bitmap_to_rgba(padded, pixels.data(), pixels.data()));
  GHOST_ClipboardBitmapLayout rgb = rgba_layout(4, 1);
  rgb.samples_per_pixel = 3;
  rgb.has_alpha = false;
  rgb.bits_per_pixel = 24;
  rgb.bytes_per_row = 12;
  EXPECT_FALSE(GHOST_ClipboardImage_bitmap_to_rgba(rgb, pixels.data(), pixels.data()));
  EXPECT_EQ(pixels, std::vector<uint8_t>(16, 7)) << "nothing written";
}

/** \} */

}  // namespace ghost_clipboard_image::tests
