/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The clipboard's image on its way into the window manager (`WM_clipboard_image_get`): GHOST's
 * readers that go through #ImBuf (`intern/ghost/intern/GHOST_ClipboardImage_ImBuf.cc`), as the
 * Windows and Wayland routes read files, encoded bytes and the bitmaps Windows hands over in
 * formats GHOST does not decode itself (through Blender's BMP reader); the size comes from the
 * header before anything is decoded, float-only pixels are converted, and the #ImBuf (with any
 * OpenEXR multilayer handle) is always freed. The image the platform decodes into is the one the
 * window manager takes (no second copy), and #IMB_allocFromBuffer refuses what it cannot
 * allocate. These need the
 * Blender kernel (colour management), which GHOST's own suite does not link.
 */

#include "testing/testing.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

#include "MEM_guardedalloc.h"

#include "BKE_gtest_base.hh"

#include "IMB_imbuf.hh"
#include "IMB_imbuf_types.hh"
#include "IMB_openexr.hh"

#include "GHOST_ClipboardImage.hh"

namespace ghost_clipboard_image::tests {

using namespace blender;

class GHOST_ClipboardImageBuffer : public bke::BlenderGTestBase {
 protected:
  static ImBuf *byte_picture(const int width, const int height)
  {
    ImBuf *ibuf = IMB_allocImBuf(width, height, ImBufFlags::ByteData);
    uint8_t *pixels = ibuf->byte_data_for_write();
    for (int i = 0; i < width * height * 4; i++) {
      pixels[i] = uint8_t(i * 7 + 3);
    }
    return ibuf;
  }

  static std::string temporary_file(const char *name, const Vector<uint8_t> &bytes)
  {
    const std::string filepath = ::testing::TempDir() + name;
    std::ofstream file(filepath, std::ios::binary | std::ios::trunc);
    file.write(reinterpret_cast<const char *>(bytes.data()), std::streamsize(bytes.size()));
    return filepath;
  }
};

TEST_F(GHOST_ClipboardImageBuffer, BytePixelsAreCopiedAndTheBufferFreed)
{
  const int64_t blocks = MEM_get_memory_blocks_in_use();
  ImBuf *ibuf = byte_picture(3, 2);
  uint8_t expected[24];
  memcpy(expected, ibuf->byte_data(), sizeof(expected));
  int width = 0, height = 0;
  uint8_t *rgba = GHOST_ClipboardImage_rgba_from_imbuf(ibuf, &width, &height);
  ASSERT_NE(rgba, nullptr);
  EXPECT_EQ(width, 3);
  EXPECT_EQ(height, 2);
  EXPECT_EQ(memcmp(rgba, expected, sizeof(expected)), 0);
  free(rgba);
  EXPECT_EQ(MEM_get_memory_blocks_in_use(), blocks) << "the ImBuf is freed";
}

TEST_F(GHOST_ClipboardImageBuffer, FloatOnlyPixelsAreConverted)
{
  /* A 16-bit PNG or TIFF, an OpenEXR or Radiance HDR file loads with float pixels only. */
  ImBuf *ibuf = IMB_allocImBuf(2, 1, ImBufFlags::FloatData);
  ASSERT_EQ(ibuf->byte_data(), nullptr);
  float *pixels = ibuf->float_data_for_write();
  for (int i = 0; i < 4; i++) {
    pixels[i] = 1.0f;
    pixels[4 + i] = i == 3 ? 1.0f : 0.0f;
  }
  int width = 0, height = 0;
  uint8_t *rgba = GHOST_ClipboardImage_rgba_from_imbuf(ibuf, &width, &height);
  ASSERT_NE(rgba, nullptr) << "never a null read of the missing byte pixels";
  EXPECT_EQ(width, 2);
  EXPECT_EQ(rgba[0], 255);
  EXPECT_EQ(rgba[3], 255);
  EXPECT_EQ(rgba[4], 0);
  EXPECT_EQ(rgba[7], 255);
  free(rgba);
}

TEST_F(GHOST_ClipboardImageBuffer, NoPixelsIsNullAndNothingLeaks)
{
  const int64_t blocks = MEM_get_memory_blocks_in_use();
  int width = -1, height = -1;
  EXPECT_EQ(GHOST_ClipboardImage_rgba_from_imbuf(nullptr, &width, &height), nullptr);
  EXPECT_EQ(GHOST_ClipboardImage_rgba_from_imbuf(
                IMB_allocImBuf(4, 4, ImBufFlags::Zero), &width, &height),
            nullptr);
  EXPECT_EQ(width, -1);
  EXPECT_EQ(MEM_get_memory_blocks_in_use(), blocks);
}

TEST_F(GHOST_ClipboardImageBuffer, MultilayerHandleIsClosed)
{
  /* A multilayer OpenEXR file loaded with #ImBufFlags::MultiLayer comes back as an ImBuf with no
   * pixels and a handle to every decoded pass, which #IMB_freeImBuf does not close. */
  const int64_t blocks = MEM_get_memory_blocks_in_use();
  ImBuf *ibuf = IMB_allocImBuf(4, 4, ImBufFlags::Zero);
  ibuf->exrhandle = IMB_exr_get_handle();
  int width = -1, height = -1;
  EXPECT_EQ(GHOST_ClipboardImage_rgba_from_imbuf(ibuf, &width, &height), nullptr);
  EXPECT_EQ(MEM_get_memory_blocks_in_use(), blocks) << "the handle is closed with the ImBuf";
}

TEST_F(GHOST_ClipboardImageBuffer, SizeComesFromTheHeaderOfAFileOrItsBytes)
{
  ImBuf *picture = byte_picture(37, 23);
  picture->ftype = IMB_FTYPE_PNG;
  const Vector<uint8_t> png = IMB_save_image_to_buffer(picture, ImBufFlags::ByteData);
  IMB_freeImBuf(picture);
  ASSERT_FALSE(png.is_empty());

  int width = 0, height = 0;
  ASSERT_TRUE(GHOST_ClipboardImage_memory_size(png.data(), png.size(), &width, &height));
  EXPECT_EQ(width, 37);
  EXPECT_EQ(height, 23);

  const std::string filepath = temporary_file("ghost_clipboard_image_probe.png", png);
  width = height = 0;
  ASSERT_TRUE(GHOST_ClipboardImage_file_size(filepath.c_str(), &width, &height));
  EXPECT_EQ(width, 37);
  EXPECT_EQ(height, 23);

  ImBuf *loaded = GHOST_ClipboardImage_file_load(filepath.c_str());
  ASSERT_NE(loaded, nullptr);
  EXPECT_EQ(loaded->x, 37);
  EXPECT_NE(loaded->byte_data(), nullptr);
  IMB_freeImBuf(loaded);
  std::remove(filepath.c_str());

  /* Not a picture, or no file. */
  const uint8_t text[] = "not a picture at all";
  width = height = -1;
  EXPECT_FALSE(GHOST_ClipboardImage_memory_size(text, sizeof(text), &width, &height));
  EXPECT_FALSE(GHOST_ClipboardImage_memory_size(nullptr, 0, &width, &height));
  EXPECT_FALSE(GHOST_ClipboardImage_file_size(filepath.c_str(), &width, &height));
  EXPECT_FALSE(GHOST_ClipboardImage_file_size(nullptr, &width, &height));
  EXPECT_EQ(width, -1);
  EXPECT_EQ(GHOST_ClipboardImage_file_load(filepath.c_str()), nullptr);
}

TEST_F(GHOST_ClipboardImageBuffer, CopyIntoAnImBufThatCannotBeAllocatedIsNull)
{
  /* #WM_clipboard_image_get copies GHOST's pixels with #IMB_allocFromBuffer. Pixels that cannot
   * be allocated (here a size past `SIZE_MAX`, refused before any allocation) are a null result,
   * never a copy into a null buffer. */
  const uint8_t bytes[4] = {1, 2, 3, 4};
  const float floats[4] = {1.0f, 0.5f, 0.25f, 1.0f};
  const uint side = 1u << 31;
  EXPECT_EQ(IMB_allocFromBuffer(bytes, nullptr, side, side, 4), nullptr);
  EXPECT_EQ(IMB_allocFromBuffer(nullptr, floats, side, side, 4), nullptr);

  ImBuf *ibuf = IMB_allocFromBuffer(bytes, floats, 1, 1, 4);
  ASSERT_NE(ibuf, nullptr);
  EXPECT_EQ(ibuf->byte_data()[2], 3);
  EXPECT_EQ(ibuf->float_data()[1], 0.5f);
  IMB_freeImBuf(ibuf);
}

/** A device-independent bitmap: a `BITMAPINFOHEADER` for \a width by \a height (bottom-up),
 * then \a after (colour table, masks, pixels). */
static std::vector<uint8_t> dib(const int width,
                                const int height,
                                const int bits,
                                const uint32_t compression,
                                const uint32_t colors_used,
                                const std::vector<uint8_t> &after)
{
  std::vector<uint8_t> bytes(40, 0);
  auto put = [&](const size_t at, const uint32_t value) {
    for (int i = 0; i < 4; i++) {
      bytes[at + i] = uint8_t(value >> (8 * i));
    }
  };
  put(0, 40);
  put(4, uint32_t(width));
  put(8, uint32_t(height));
  bytes[12] = 1;
  bytes[14] = uint8_t(bits);
  put(16, compression);
  put(32, colors_used);
  bytes.insert(bytes.end(), after.begin(), after.end());
  return bytes;
}

/** Pixel \a i of \a rgba as {r, g, b, a}. */
static std::vector<int> pixel(const uint8_t *rgba, const int i)
{
  return {rgba[i * 4], rgba[i * 4 + 1], rgba[i * 4 + 2], rgba[i * 4 + 3]};
}

/** Pixel \a i of \a ibuf's bytes as {r, g, b, a}. */
static std::vector<int> pixel(const ImBuf *ibuf, const int i)
{
  return pixel(ibuf->byte_data(), i);
}

TEST_F(GHOST_ClipboardImageBuffer, BitmapsInOtherFormatsAreReadByTheBmpReader)
{
  /* A two-colour palette (`RGBQUAD`: blue, green, red, reserved): 0 is blue, 1 is red. */
  const std::vector<uint8_t> palette = {255, 0, 0, 0, 0, 0, 255, 0};
  const std::vector<int> red = {255, 0, 0, 255}, blue = {0, 0, 255, 255};

  /* 8-bit: bottom row red, blue; top row blue, red; rows padded to 4 bytes. */
  std::vector<uint8_t> after = palette;
  after.insert(after.end(), {1, 0, 0, 0, 0, 1, 0, 0});
  std::vector<uint8_t> bytes = dib(2, 2, 8, 0, 2, after);
  ImBuf *ibuf = GHOST_ClipboardImage_dib_load(bytes.data(), bytes.size());
  ASSERT_NE(ibuf, nullptr) << "an 8-bit bitmap is read, not refused";
  EXPECT_EQ(ibuf->x, 2);
  EXPECT_EQ(ibuf->y, 2);
  EXPECT_EQ(pixel(ibuf, 0), red);
  EXPECT_EQ(pixel(ibuf, 1), blue);
  EXPECT_EQ(pixel(ibuf, 2), blue);
  EXPECT_EQ(pixel(ibuf, 3), red);
  IMB_freeImBuf(ibuf);

  /* The same picture run-length encoded (`BI_RLE8`). */
  after = palette;
  /* Bottom row: one red, one blue, end of line; top row: one blue, one red, end of bitmap. */
  after.insert(after.end(), {1, 1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 1});
  bytes = dib(2, 2, 8, 1, 2, after);
  ibuf = GHOST_ClipboardImage_dib_load(bytes.data(), bytes.size());
  ASSERT_NE(ibuf, nullptr) << "run-length encoded";
  EXPECT_EQ(pixel(ibuf, 0), red);
  EXPECT_EQ(pixel(ibuf, 1), blue);
  EXPECT_EQ(pixel(ibuf, 2), blue);
  EXPECT_EQ(pixel(ibuf, 3), red);
  IMB_freeImBuf(ibuf);

  /* 16-bit 5:6:5 (`BI_BITFIELDS`, masks after the header): one red pixel. */
  after = {0x00, 0xf8, 0, 0, 0xe0, 0x07, 0, 0, 0x1f, 0, 0, 0, /* Pixel. */ 0x00, 0xf8, 0, 0};
  bytes = dib(1, 1, 16, 3, 0, after);
  ibuf = GHOST_ClipboardImage_dib_load(bytes.data(), bytes.size());
  ASSERT_NE(ibuf, nullptr) << "16-bit";
  EXPECT_EQ(pixel(ibuf, 0), red);
  IMB_freeImBuf(ibuf);

  /* What neither reader reads stays refused: an embedded JPEG, or a palette past the data. */
  bytes = dib(2, 2, 0, 4, 0, std::vector<uint8_t>(64, 0));
  EXPECT_EQ(GHOST_ClipboardImage_dib_load(bytes.data(), bytes.size()), nullptr);
  bytes = dib(2, 2, 8, 0, 0, palette); /* 256 entries are expected, two are there. */
  EXPECT_EQ(GHOST_ClipboardImage_dib_load(bytes.data(), bytes.size()), nullptr);
}

TEST_F(GHOST_ClipboardImageBuffer, TheImageHoldsTheOnlyCopyOfThePixels)
{
  /* `WM_clipboard_image_get` takes the image the platform decoded into: a bitmap is decoded
   * straight into its pixels, and a decoded file keeps its own (no second copy). */
  const int64_t blocks = MEM_get_memory_blocks_in_use();
  const std::vector<uint8_t> bytes = dib(
      2, 1, 32, 0, 0, {/* `RGBQUAD`s, opaque. */ 1, 2, 3, 255, 4, 5, 6, 255});
  ImBuf *ibuf = GHOST_ClipboardImage_dib_load(bytes.data(), bytes.size());
  ASSERT_NE(ibuf, nullptr);
  EXPECT_EQ(pixel(ibuf, 0), (std::vector<int>{3, 2, 1, 255}));
  EXPECT_EQ(pixel(ibuf, 1), (std::vector<int>{6, 5, 4, 255}));
  IMB_freeImBuf(ibuf);
  EXPECT_EQ(MEM_get_memory_blocks_in_use(), blocks);

  ImBuf *picture = byte_picture(3, 2);
  const uint8_t *pixels = picture->byte_data();
  EXPECT_EQ(GHOST_ClipboardImage_flatten(picture), picture);
  EXPECT_EQ(picture->byte_data(), pixels) << "the same pixels, not a copy";
  IMB_freeImBuf(picture);

  /* Float-only: bytes made, the float pixels (four times larger) freed at once. */
  ImBuf *deep = IMB_allocImBuf(2, 1, ImBufFlags::FloatData);
  ASSERT_EQ(GHOST_ClipboardImage_flatten(deep), deep);
  EXPECT_NE(deep->byte_data(), nullptr);
  EXPECT_EQ(deep->float_data(), nullptr);
  IMB_freeImBuf(deep);
  EXPECT_EQ(GHOST_ClipboardImage_flatten(IMB_allocImBuf(2, 2, ImBufFlags::Zero)), nullptr);
  EXPECT_EQ(MEM_get_memory_blocks_in_use(), blocks);
}

}  // namespace ghost_clipboard_image::tests
