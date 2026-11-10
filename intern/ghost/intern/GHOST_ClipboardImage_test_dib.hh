/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Device-independent bitmaps built byte by byte for the clipboard picture tests
 * (`GHOST_ClipboardImage_test.cc`, `GHOST_ClipboardImage_bmp_test.cc`).
 */

#pragma once

#include <cstdint>
#include <vector>

#include "GHOST_ClipboardImage.hh"

namespace ghost_clipboard_image::tests {

/* `DIB_*`, never `BI_*`: those are macros in `wingdi.h`. */
constexpr uint32_t DIB_RGB = 0;
constexpr uint32_t DIB_RLE8 = 1;
constexpr uint32_t DIB_BITFIELDS = 3;
constexpr uint32_t DIB_JPEG = 4;
constexpr uint32_t DIB_PNG = 5;
constexpr uint32_t DIB_V5_HEADER = 124;
constexpr uint32_t DIB_INFO_HEADER = 40;

inline void put_u32(std::vector<uint8_t> &bytes, const size_t at, const uint32_t value)
{
  for (int i = 0; i < 4; i++) {
    bytes[at + i] = uint8_t(value >> (8 * i));
  }
}

struct Dib {
  uint32_t header_bytes = DIB_V5_HEADER;
  int32_t width = 1;
  int32_t height = 1;
  uint16_t bit_count = 32;
  uint32_t compression = DIB_RGB;
  uint32_t colors_used = 0;
  uint32_t masks[4] = {0, 0, 0, 0};

  /** The header (masks included when it has room), then \a after. */
  std::vector<uint8_t> bytes(const std::vector<uint8_t> &after = {}) const
  {
    std::vector<uint8_t> result(header_bytes, 0);
    put_u32(result, 0, header_bytes);
    put_u32(result, 4, uint32_t(width));
    put_u32(result, 8, uint32_t(height));
    result[12] = 1; /* Planes. */
    result[14] = uint8_t(bit_count);
    result[15] = uint8_t(bit_count >> 8);
    put_u32(result, 16, compression);
    put_u32(result, 32, colors_used);
    if (header_bytes >= 56) {
      for (int i = 0; i < 4; i++) {
        put_u32(result, 40 + 4 * i, masks[i]);
      }
    }
    result.insert(result.end(), after.begin(), after.end());
    return result;
  }
};

/** Decode \a bytes; the result is freed by the caller. */
inline uint8_t *decode(const std::vector<uint8_t> &bytes, int &width, int &height)
{
  return GHOST_ClipboardImage_dib_decode(bytes.data(), bytes.size(), &width, &height);
}

}  // namespace ghost_clipboard_image::tests
