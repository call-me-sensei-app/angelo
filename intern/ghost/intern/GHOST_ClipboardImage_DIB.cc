/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 *
 * Windows device-independent bitmaps (`CF_DIB`, `CF_DIBV5`) read from their bytes (see
 * `GHOST_ClipboardImage.hh`). No platform headers: every platform compiles and tests this file.
 */

#include "GHOST_ClipboardImage.hh"

#include <climits>
#include <cstdlib>

/* -------------------------------------------------------------------- */
/** \name Windows Device-Independent Bitmaps
 * \{ */

namespace {

/** `BITMAPINFOHEADER`: the smallest header, without masks. */
constexpr uint32_t DIB_INFO_HEADER_BYTES = 40;
/** A header this long holds the red, green and blue masks (`BITMAPV2INFOHEADER` and later). */
constexpr uint32_t DIB_RGB_MASKS_END = 52;
/** A header this long also holds the alpha mask (`BITMAPV3INFOHEADER`, V4, V5). */
constexpr uint32_t DIB_ALPHA_MASK_END = 56;
constexpr uint32_t DIB_BI_RGB = 0;
constexpr uint32_t DIB_BI_RLE8 = 1;
constexpr uint32_t DIB_BI_RLE4 = 2;
constexpr uint32_t DIB_BI_BITFIELDS = 3;
/** The three `DWORD` colour masks that follow a `BITMAPINFOHEADER` for `BI_BITFIELDS`. */
constexpr uint64_t DIB_MASK_BYTES = 3 * 4;
/** A DIB's pixel data is counted in a `DWORD` (`biSizeImage`). */
constexpr uint64_t DIB_MAX_IMAGE_BYTES = UINT32_MAX;

uint16_t read_u16(const uint8_t *bytes)
{
  return uint16_t(bytes[0] | (bytes[1] << 8));
}

uint32_t read_u32(const uint8_t *bytes)
{
  return uint32_t(bytes[0]) | (uint32_t(bytes[1]) << 8) | (uint32_t(bytes[2]) << 16) |
         (uint32_t(bytes[3]) << 24);
}

struct DibHeader {
  uint32_t header_bytes = 0;
  int64_t width = 0;
  /** Always positive, see #top_down. */
  int64_t height = 0;
  bool top_down = false;
  uint16_t bit_count = 0;
  uint32_t compression = 0;
  uint32_t colors_used = 0;
  /** Red, green, blue and alpha masks from the header, 0 where the header has none. */
  uint32_t masks[4] = {0, 0, 0, 0};
};

bool dib_read_header(const uint8_t *dib, const size_t dib_size, DibHeader &r_header)
{
  if (dib == nullptr || dib_size < DIB_INFO_HEADER_BYTES) {
    return false;
  }
  r_header.header_bytes = read_u32(dib);
  if (r_header.header_bytes < DIB_INFO_HEADER_BYTES || r_header.header_bytes > dib_size) {
    return false;
  }
  const int32_t width = int32_t(read_u32(dib + 4));
  const int32_t height = int32_t(read_u32(dib + 8));
  /* A negative height is a top-down bitmap; INT32_MIN has no positive counterpart. */
  if (width <= 0 || height == 0 || height == INT32_MIN) {
    return false;
  }
  r_header.width = width;
  r_header.height = height < 0 ? -int64_t(height) : int64_t(height);
  r_header.top_down = height < 0;
  r_header.bit_count = read_u16(dib + 14);
  r_header.compression = read_u32(dib + 16);
  r_header.colors_used = read_u32(dib + 32);
  if (r_header.header_bytes >= DIB_RGB_MASKS_END) {
    r_header.masks[0] = read_u32(dib + 40);
    r_header.masks[1] = read_u32(dib + 44);
    r_header.masks[2] = read_u32(dib + 48);
  }
  if (r_header.header_bytes >= DIB_ALPHA_MASK_END) {
    r_header.masks[3] = read_u32(dib + 52);
  }
  return true;
}

int mask_shift(const uint32_t mask)
{
  int shift = 0;
  while (shift < 31 && ((mask >> shift) & 1u) == 0) {
    shift++;
  }
  return shift;
}

/** The channel \a mask selects from \a pixel, scaled to 0-255 whatever the mask's width. */
uint8_t mask_channel(const uint32_t pixel, const uint32_t mask, const int shift)
{
  const uint32_t max = mask >> shift;
  const uint32_t value = (pixel & mask) >> shift;
  if (max == 0xff) {
    return uint8_t(value);
  }
  return uint8_t((uint64_t(value) * 255 + max / 2) / max);
}

/**
 * Whether the three `DWORD`s at \a offset repeat a V4/V5 header's colour masks before the pixels.
 * Some applications (Windows Snipping Tool) write the masks both in the header and after it,
 * others (Krita, Paint.NET) only in the header. They are a repeat only when they equal the
 * header's (non-zero) masks and the pixels still fit after them; bytes to spare alone are no
 * repeat.
 */
bool dib_repeats_masks(const uint8_t *dib,
                       const size_t dib_size,
                       const uint64_t offset,
                       const uint64_t image_bytes,
                       const uint32_t header_masks[4])
{
  if (offset > dib_size || uint64_t(dib_size) - offset < DIB_MASK_BYTES + image_bytes) {
    return false;
  }
  for (int i = 0; i < 3; i++) {
    if (header_masks[i] == 0 || read_u32(dib + offset + 4 * i) != header_masks[i]) {
      return false;
    }
  }
  return true;
}

/** Whether the BMP reader (OpenImageIO 3.1, `bmp_pvt.cpp`) parses a header of this size. */
bool bmp_reader_header(const uint32_t header_bytes)
{
  return header_bytes == 40 || header_bytes == 52 || header_bytes == 56 || header_bytes == 108 ||
         header_bytes == 124;
}

/** A decodable bitmap's layout, every offset checked against the bytes (#dib_plan). */
struct DibPlan {
  DibHeader header;
  bool rgb24 = false;
  bool rgb32 = false;
  bool fields32 = false;
  /** Red, green, blue, alpha; alpha 0 when there is no fourth channel. */
  uint32_t masks[4] = {0, 0, 0, 0};
  int shifts[4] = {0, 0, 0, 0};
  uint64_t offset = 0;
  uint64_t row_bytes = 0;
};

/** Plan the decode of a 24-bit or 32-bit `BI_RGB` or 32-bit `BI_BITFIELDS` bitmap. */
bool dib_plan(const uint8_t *dib, const size_t dib_size, DibPlan &r_plan)
{
  DibHeader &header = r_plan.header;
  if (!dib_read_header(dib, dib_size, header)) {
    return false;
  }
  r_plan.rgb24 = header.compression == DIB_BI_RGB && header.bit_count == 24;
  r_plan.rgb32 = header.compression == DIB_BI_RGB && header.bit_count == 32;
  r_plan.fields32 = header.compression == DIB_BI_BITFIELDS && header.bit_count == 32;
  if (!(r_plan.rgb24 || r_plan.rgb32 || r_plan.fields32)) {
    return false; /* Never an unread buffer passed off as a picture. */
  }

  uint32_t *masks = r_plan.masks;
  for (int i = 0; i < 4; i++) {
    masks[i] = header.masks[i];
  }
  uint64_t offset = header.header_bytes;
  if (r_plan.fields32 && header.header_bytes < DIB_RGB_MASKS_END) {
    /* A `BITMAPINFOHEADER`: the masks follow it. */
    if (dib_size - offset < DIB_MASK_BYTES) {
      return false;
    }
    masks[0] = read_u32(dib + offset);
    masks[1] = read_u32(dib + offset + 4);
    masks[2] = read_u32(dib + offset + 8);
    offset += DIB_MASK_BYTES;
  }
  /* An optional colour table (unused at these depths) precedes the pixels. 64-bit: at most
   * 2^32 entries of 4 bytes. */
  offset += uint64_t(header.colors_used) * 4;

  /* Each row is padded to 4 bytes. */
  const uint64_t row_bytes = ((uint64_t(header.width) * header.bit_count + 31) / 32) * 4;
  if (row_bytes > DIB_MAX_IMAGE_BYTES || uint64_t(header.height) > DIB_MAX_IMAGE_BYTES / row_bytes)
  {
    return false;
  }
  const uint64_t image_bytes = row_bytes * uint64_t(header.height);
  if (r_plan.fields32 && header.header_bytes >= DIB_RGB_MASKS_END &&
      dib_repeats_masks(dib, dib_size, offset, image_bytes, header.masks))
  {
    offset += DIB_MASK_BYTES;
  }
  if (offset > dib_size || image_bytes > uint64_t(dib_size) - offset) {
    return false; /* Fewer bytes than the header says: never read past them. */
  }
  if (GHOST_ClipboardImage_rgba_size(header.width, header.height) == 0) {
    return false;
  }

  if (r_plan.fields32) {
    /* Missing colour masks: the byte order the clipboard has historically been read in. */
    const uint32_t defaults[3] = {0xffu, 0xff00u, 0xff0000u};
    for (int i = 0; i < 3; i++) {
      masks[i] = masks[i] ? masks[i] : defaults[i];
    }
    /* The fourth channel: the alpha mask, or else the top byte when no colour uses it. */
    if (masks[3] == 0 && ((masks[0] | masks[1] | masks[2]) & 0xff000000u) == 0) {
      masks[3] = 0xff000000u;
    }
    for (int i = 0; i < 4; i++) {
      r_plan.shifts[i] = masks[i] ? mask_shift(masks[i]) : 0;
    }
  }
  r_plan.offset = offset;
  r_plan.row_bytes = row_bytes;
  return true;
}

/**
 * Settle the fourth channel of a 32-bit bitmap, as Chromium reads `CF_DIB` (`clipboard_win.cc`,
 * `BitmapHasInvalidPremultipliedColors`): Windows stores premultiplied alpha, so a colour larger
 * than its alpha means the channel is not alpha (padding, often 0 from GDI, or garbage) and the
 * picture is opaque; otherwise the colour is divided by alpha. One addition: alpha that is 0 in
 * every pixel is opaque too, so even an all-black bitmap with zero padding is never invisible.
 */
void dib_settle_alpha(uint8_t *rgba, const size_t rgba_bytes)
{
  bool any_alpha = false;
  bool premultiplied = true;
  for (size_t i = 0; i < rgba_bytes; i += 4) {
    const uint8_t alpha = rgba[i + 3];
    any_alpha |= alpha != 0;
    if (rgba[i] > alpha || rgba[i + 1] > alpha || rgba[i + 2] > alpha) {
      premultiplied = false;
      break;
    }
  }
  if (!any_alpha || !premultiplied) {
    for (size_t i = 3; i < rgba_bytes; i += 4) {
      rgba[i] = 255;
    }
    return;
  }
  for (size_t i = 0; i < rgba_bytes; i += 4) {
    const uint32_t alpha = rgba[i + 3];
    if (alpha != 0 && alpha != 255) {
      for (int c = 0; c < 3; c++) {
        rgba[i + c] = uint8_t((rgba[i + c] * 255u + alpha / 2) / alpha); /* Colour <= alpha. */
      }
    }
  }
}

}  // namespace

bool GHOST_ClipboardImage_dib_size(const uint8_t *dib,
                                   const size_t dib_size,
                                   int *r_width,
                                   int *r_height)
{
  DibHeader header;
  if (!dib_read_header(dib, dib_size, header)) {
    return false;
  }
  *r_width = int(header.width);
  *r_height = int(header.height);
  return true;
}

bool GHOST_ClipboardImage_dib_decodable(const uint8_t *dib,
                                        const size_t dib_size,
                                        int *r_width,
                                        int *r_height)
{
  DibPlan plan;
  if (!dib_plan(dib, dib_size, plan)) {
    return false;
  }
  *r_width = int(plan.header.width);
  *r_height = int(plan.header.height);
  return true;
}

bool GHOST_ClipboardImage_dib_decode_into(const uint8_t *dib, const size_t dib_size, uint8_t *rgba)
{
  DibPlan plan;
  if (rgba == nullptr || !dib_plan(dib, dib_size, plan)) {
    return false;
  }
  const DibHeader &header = plan.header;
  const size_t rgba_bytes = GHOST_ClipboardImage_rgba_size(header.width, header.height);
  const uint8_t *pixels = dib + plan.offset;
  const int64_t width = header.width;
  for (int64_t y = 0; y < header.height; y++) {
    /* Bottom-up bitmaps store the bottom row first, as Blender does; top-down ones are flipped. */
    const int64_t source_row = header.top_down ? header.height - 1 - y : y;
    const uint8_t *source = pixels +
                            GHOST_ClipboardImage_row_offset(source_row, int64_t(plan.row_bytes));
    uint8_t *target = rgba + GHOST_ClipboardImage_row_offset(y, width * 4);
    for (int64_t x = 0; x < width; x++, target += 4) {
      if (plan.rgb24) {
        const uint8_t *triple = source + x * 3; /* `RGBTRIPLE`: blue, green, red. */
        target[0] = triple[2];
        target[1] = triple[1];
        target[2] = triple[0];
        target[3] = 255;
      }
      else if (plan.rgb32) {
        const uint8_t *quad = source + x * 4; /* `RGBQUAD`: blue, green, red, reserved. */
        target[0] = quad[2];
        target[1] = quad[1];
        target[2] = quad[0];
        target[3] = quad[3];
      }
      else {
        const uint32_t pixel = read_u32(source + x * 4);
        for (int i = 0; i < 3; i++) {
          target[i] = mask_channel(pixel, plan.masks[i], plan.shifts[i]);
        }
        target[3] = plan.masks[3] ? mask_channel(pixel, plan.masks[3], plan.shifts[3]) : 255;
      }
    }
  }
  if (plan.rgb32 || plan.fields32) {
    dib_settle_alpha(rgba, rgba_bytes);
  }
  return true;
}

uint8_t *GHOST_ClipboardImage_dib_decode(const uint8_t *dib,
                                         const size_t dib_size,
                                         int *r_width,
                                         int *r_height)
{
  int width = 0, height = 0;
  if (!GHOST_ClipboardImage_dib_decodable(dib, dib_size, &width, &height)) {
    return nullptr;
  }
  uint8_t *rgba = static_cast<uint8_t *>(malloc(GHOST_ClipboardImage_rgba_size(width, height)));
  if (rgba == nullptr) {
    return nullptr;
  }
  GHOST_ClipboardImage_dib_decode_into(dib, dib_size, rgba);
  *r_width = width;
  *r_height = height;
  return rgba;
}

bool GHOST_ClipboardImage_dib_file_header(
    const uint8_t *dib,
    const size_t dib_size,
    uint8_t r_file_header[GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES])
{
  DibHeader header;
  if (!dib_read_header(dib, dib_size, header)) {
    return false;
  }
  const uint32_t compression = header.compression;
  const uint16_t bits = header.bit_count;
  const bool indexed = (bits == 1 || bits == 4 || bits == 8) && compression == DIB_BI_RGB;
  const bool rle = (bits == 8 && compression == DIB_BI_RLE8) ||
                   (bits == 4 && compression == DIB_BI_RLE4);
  const bool fields16 = bits == 16 &&
                        (compression == DIB_BI_RGB || compression == DIB_BI_BITFIELDS);
  if (!(indexed || rle || fields16)) {
    return false; /* Read by #GHOST_ClipboardImage_dib_decode, or by no reader. */
  }
  /* The BMP reader (OpenImageIO) parses only these header sizes; with any other its sides and
   * depth would be unread, so the size checked here would not bound what it allocates. */
  if (!bmp_reader_header(header.header_bytes)) {
    return false;
  }
  /* Run-length bitmaps are bottom-up only (a negative height is invalid for them). */
  if (rle && header.top_down) {
    return false;
  }
  uint64_t palette = 0;
  if (bits <= 8) {
    const uint64_t most = uint64_t(1) << bits;
    if (header.colors_used > most) {
      return false;
    }
    palette = header.colors_used ? header.colors_used : most;
  }
  else {
    palette = header.colors_used; /* An optional table, skipped. */
  }
  uint64_t offset = uint64_t(header.header_bytes) + palette * 4;
  if (compression == DIB_BI_BITFIELDS && header.header_bytes < DIB_RGB_MASKS_END) {
    offset += DIB_MASK_BYTES; /* The masks follow a `BITMAPINFOHEADER`. */
  }
  if (offset > dib_size) {
    return false;
  }
  if (!rle) {
    /* Uncompressed rows, each padded to 4 bytes, must all be there. */
    const uint64_t row_bytes = ((uint64_t(header.width) * bits + 31) / 32) * 4;
    if (row_bytes > DIB_MAX_IMAGE_BYTES ||
        uint64_t(header.height) > DIB_MAX_IMAGE_BYTES / row_bytes)
    {
      return false;
    }
    const uint64_t image_bytes = row_bytes * uint64_t(header.height);
    if (fields16 && compression == DIB_BI_BITFIELDS && header.header_bytes >= DIB_RGB_MASKS_END &&
        dib_repeats_masks(dib, dib_size, offset, image_bytes, header.masks))
    {
      offset += DIB_MASK_BYTES; /* Masks repeated after a V4/V5 header, as for 32-bit. */
    }
    if (image_bytes > uint64_t(dib_size) - offset) {
      return false;
    }
  }
  const uint64_t file_bytes = GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES + uint64_t(dib_size);
  if (file_bytes > UINT32_MAX) {
    return false;
  }
  const uint32_t pixels_at = uint32_t(GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES + offset);
  uint8_t *out = r_file_header;
  out[0] = 'B';
  out[1] = 'M';
  for (int i = 0; i < 4; i++) {
    out[2 + i] = uint8_t(uint32_t(file_bytes) >> (8 * i)); /* `bfSize`. */
    out[6 + i] = 0;                                        /* `bfReserved1`, `bfReserved2`. */
    out[10 + i] = uint8_t(pixels_at >> (8 * i));           /* `bfOffBits`. */
  }
  return true;
}

/** \} */
