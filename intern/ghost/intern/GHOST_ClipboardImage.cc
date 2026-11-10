/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 *
 * Byte-level readers of clipboard pictures (see `GHOST_ClipboardImage.hh`): sizes and bitmap rows.
 * Windows device-independent bitmaps are in `GHOST_ClipboardImage_DIB.cc`. No platform headers:
 * every platform compiles and tests these files.
 */

#include "GHOST_ClipboardImage.hh"

#include <algorithm>
#include <climits>
#include <cstdlib>
#include <cstring>

/* -------------------------------------------------------------------- */
/** \name Sizes
 * \{ */

size_t GHOST_ClipboardImage_rgba_size(const int64_t width, const int64_t height)
{
  if (width < 1 || height < 1 || width > INT_MAX || height > INT_MAX) {
    return 0;
  }
  const uint64_t pixels = uint64_t(width) * uint64_t(height); /* At most 2^62. */
  if (pixels > SIZE_MAX / 4) {
    return 0;
  }
  return size_t(pixels * 4);
}

size_t GHOST_ClipboardImage_row_offset(const int64_t row, const int64_t row_bytes)
{
  if (row < 0 || row_bytes < 0) {
    return SIZE_MAX;
  }
  if (row_bytes != 0 && uint64_t(row) > uint64_t(SIZE_MAX) / uint64_t(row_bytes)) {
    return SIZE_MAX;
  }
  return size_t(uint64_t(row) * uint64_t(row_bytes));
}

size_t GHOST_ClipboardImage_windows_formats(const uint32_t *formats,
                                            const size_t count,
                                            const uint32_t cf_png,
                                            const uint32_t cf_dibv5,
                                            uint32_t r_order[2])
{
  bool png = false, dibv5 = false;
  for (size_t i = 0; i < count; i++) {
    png |= formats[i] == cf_png;
    dibv5 |= formats[i] == cf_dibv5;
  }
  size_t found = 0;
  if (png) {
    r_order[found++] = cf_png;
  }
  if (dibv5) {
    r_order[found++] = cf_dibv5;
  }
  return found;
}

bool GHOST_ClipboardImage_png_size(const uint8_t *data,
                                   const size_t data_size,
                                   int *r_width,
                                   int *r_height)
{
  static const uint8_t signature[8] = {137, 80, 78, 71, 13, 10, 26, 10};
  if (data == nullptr || data_size < GHOST_CLIPBOARD_PNG_HEADER_BYTES ||
      memcmp(data, signature, sizeof(signature)) != 0 || memcmp(data + 12, "IHDR", 4) != 0)
  {
    return false;
  }
  auto read_be32 = [](const uint8_t *bytes) {
    return (uint32_t(bytes[0]) << 24) | (uint32_t(bytes[1]) << 16) | (uint32_t(bytes[2]) << 8) |
           uint32_t(bytes[3]);
  };
  const uint32_t width = read_be32(data + 16);
  const uint32_t height = read_be32(data + 20);
  if (GHOST_ClipboardImage_rgba_size(width, height) == 0) {
    return false;
  }
  *r_width = int(width);
  *r_height = int(height);
  return true;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Bitmap Rows
 * \{ */

bool GHOST_ClipboardImage_bitmap_readable(const GHOST_ClipboardBitmapLayout &layout)
{
  if (GHOST_ClipboardImage_rgba_size(layout.width, layout.height) == 0) {
    return false;
  }
  if (layout.planar || layout.float_samples || !layout.rgb || layout.bits_per_sample != 8) {
    return false;
  }
  const bool rgb = layout.samples_per_pixel == 3 && !layout.has_alpha;
  const bool rgba = layout.samples_per_pixel == 4 && layout.has_alpha;
  if (!(rgb || rgba)) {
    return false;
  }
  /* Three samples in 24 bits, or in 32 with a spare byte; four samples in 32 bits. */
  if (!(layout.bits_per_pixel == 32 || (rgb && layout.bits_per_pixel == 24))) {
    return false;
  }
  const int64_t pixel_bytes = layout.bits_per_pixel / 8;
  if (layout.bytes_per_row < layout.width * pixel_bytes) {
    return false;
  }
  return GHOST_ClipboardImage_row_offset(layout.height, layout.bytes_per_row) != SIZE_MAX;
}

bool GHOST_ClipboardImage_draw_layout(const int64_t width,
                                      const int64_t height,
                                      const bool premultiplied,
                                      GHOST_ClipboardBitmapLayout &r_layout)
{
  if (GHOST_ClipboardImage_rgba_size(width, height) == 0) {
    return false;
  }
  GHOST_ClipboardBitmapLayout layout;
  layout.width = width;
  layout.height = height;
  layout.bytes_per_row = width * 4; /* Explicit and tight: the context never pads its rows. */
  layout.bits_per_pixel = 32;
  layout.bits_per_sample = 8;
  layout.samples_per_pixel = 4;
  layout.has_alpha = true;
  layout.alpha_first = false;
  layout.alpha_premultiplied = premultiplied;
  layout.rgb = true;
  layout.top_row_first = true;
  r_layout = layout;
  return true;
}

namespace {

/** Offsets of colour and alpha in one pixel of a readable layout. */
struct PixelReader {
  int64_t pixel_bytes;
  int64_t color_at;
  int64_t alpha_at;
  bool has_alpha;
  bool unpremultiply;

  explicit PixelReader(const GHOST_ClipboardBitmapLayout &layout)
      : pixel_bytes(layout.bits_per_pixel / 8),
        /* Alpha (or the spare byte) first moves the colour one byte on. */
        color_at((layout.alpha_first && layout.bits_per_pixel == 32) ? 1 : 0),
        alpha_at(layout.alpha_first ? 0 : 3),
        has_alpha(layout.has_alpha),
        unpremultiply(layout.has_alpha && layout.alpha_premultiplied)
  {
  }

  /** Read the pixel at \a source as straight RGBA into \a target (may be the same bytes). */
  void read(const uint8_t *source, uint8_t target[4]) const
  {
    const uint8_t alpha = has_alpha ? source[alpha_at] : 255;
    uint8_t color[3];
    for (int c = 0; c < 3; c++) {
      uint32_t value = source[color_at + c];
      if (unpremultiply && alpha != 255) {
        value = alpha == 0 ? 0 : std::min<uint32_t>(255, (value * 255 + alpha / 2) / alpha);
      }
      color[c] = uint8_t(value);
    }
    target[0] = color[0];
    target[1] = color[1];
    target[2] = color[2];
    target[3] = alpha;
  }
};

}  // namespace

bool GHOST_ClipboardImage_bitmap_to_rgba(const GHOST_ClipboardBitmapLayout &layout,
                                         const uint8_t *pixels,
                                         uint8_t *rgba)
{
  if (pixels == nullptr || rgba == nullptr || !GHOST_ClipboardImage_bitmap_readable(layout)) {
    return false;
  }
  const PixelReader reader(layout);
  const int64_t target_row_bytes = layout.width * 4;

  if (pixels == rgba) {
    /* In place: only rows of tight four-byte pixels, which have the same size in and out. Rows
     * are swapped in pairs (flipping top-first rows) and each pixel is read before either
     * position is written. */
    if (reader.pixel_bytes != 4 || layout.bytes_per_row != target_row_bytes) {
      return false;
    }
    const int64_t half = layout.top_row_first ? layout.height / 2 : 0;
    for (int64_t y = 0; y < half; y++) {
      uint8_t *top = rgba + GHOST_ClipboardImage_row_offset(y, target_row_bytes);
      uint8_t *bottom = rgba +
                        GHOST_ClipboardImage_row_offset(layout.height - 1 - y, target_row_bytes);
      for (int64_t x = 0; x < layout.width; x++, top += 4, bottom += 4) {
        uint8_t from_top[4], from_bottom[4];
        reader.read(top, from_top);
        reader.read(bottom, from_bottom);
        memcpy(top, from_bottom, 4);
        memcpy(bottom, from_top, 4);
      }
    }
    /* The middle row of an odd height, or every row when nothing is flipped. */
    for (int64_t y = half; y < layout.height - half; y++) {
      uint8_t *row = rgba + GHOST_ClipboardImage_row_offset(y, target_row_bytes);
      for (int64_t x = 0; x < layout.width; x++, row += 4) {
        reader.read(row, row);
      }
    }
    return true;
  }

  for (int64_t y = 0; y < layout.height; y++) {
    const int64_t source_row = layout.top_row_first ? layout.height - 1 - y : y;
    const uint8_t *source = pixels +
                            GHOST_ClipboardImage_row_offset(source_row, layout.bytes_per_row);
    uint8_t *target = rgba + GHOST_ClipboardImage_row_offset(y, target_row_bytes);
    for (int64_t x = 0; x < layout.width; x++, source += reader.pixel_bytes, target += 4) {
      reader.read(source, target);
    }
  }
  return true;
}

/** \} */
