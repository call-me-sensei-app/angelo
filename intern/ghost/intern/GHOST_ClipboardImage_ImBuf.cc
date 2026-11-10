/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 *
 * Clipboard pictures in a file format, read through #ImBuf (see `GHOST_ClipboardImage.hh`): a
 * copied file (Windows `CF_HDROP`, Wayland `text/uri-list`) or encoded bytes (Windows "PNG",
 * Wayland `image/png`).
 */

#include <cstdlib>
#include <cstring>

#include "IMB_imbuf.hh"
#include "IMB_imbuf_types.hh"
#include "IMB_openexr.hh"

#include "GHOST_ClipboardImage.hh"

/** Header only: the sides, no pixels. */
static constexpr blender::ImBufFlags PROBE_FLAGS = blender::ImBufFlags::ByteData |
                                                   blender::ImBufFlags::Test;

static bool probed_size(blender::ImBuf *ibuf, int *r_width, int *r_height)
{
  if (ibuf == nullptr) {
    return false;
  }
  const bool found = ibuf->x > 0 && ibuf->y > 0;
  if (found) {
    *r_width = ibuf->x;
    *r_height = ibuf->y;
  }
  blender::IMB_freeImBuf(ibuf);
  return found;
}

bool GHOST_ClipboardImage_file_size(const char *filepath, int *r_width, int *r_height)
{
  if (filepath == nullptr) {
    return false;
  }
  return probed_size(
      blender::IMB_load_image_from_filepath(filepath, PROBE_FLAGS), r_width, r_height);
}

bool GHOST_ClipboardImage_memory_size(const uint8_t *data,
                                      const size_t data_size,
                                      int *r_width,
                                      int *r_height)
{
  if (data == nullptr || data_size == 0) {
    return false;
  }
  return probed_size(
      blender::IMB_load_image_from_memory(data, data_size, PROBE_FLAGS, "<clipboard>"),
      r_width,
      r_height);
}

blender::ImBuf *GHOST_ClipboardImage_file_load(const char *filepath)
{
  if (filepath == nullptr) {
    return nullptr;
  }
  /* Never #ImBufFlags::MultiLayer: a multilayer OpenEXR file would come back as a handle to its
   * decoded passes and no picture. Without the flag such a file is not loaded at all. */
  return blender::IMB_load_image_from_filepath(filepath, blender::ImBufFlags::ByteData);
}

blender::ImBuf *GHOST_ClipboardImage_flatten(blender::ImBuf *ibuf)
{
  if (ibuf == nullptr) {
    return nullptr;
  }
  if (ibuf->exrhandle) {
    /* #IMB_freeImBuf does not close it; the passes it decoded would leak with every paste. */
    blender::IMB_exr_close(ibuf->exrhandle);
    ibuf->exrhandle = nullptr;
  }
  if (ibuf->byte_data() == nullptr) {
    /* High bit depth and HDR files (16-bit PNG and TIFF, OpenEXR, Radiance HDR) load as float
     * only. Nothing happens without float pixels either. */
    blender::IMB_byte_from_float(ibuf);
  }
  if (ibuf->byte_data() == nullptr || GHOST_ClipboardImage_rgba_size(ibuf->x, ibuf->y) == 0) {
    blender::IMB_freeImBuf(ibuf);
    return nullptr;
  }
  /* The bytes are the picture now: the float pixels (four times their size) go at once. */
  blender::IMB_free_float_pixels(ibuf);
  return ibuf;
}

uint8_t *GHOST_ClipboardImage_rgba_from_imbuf(blender::ImBuf *ibuf, int *r_width, int *r_height)
{
  ibuf = GHOST_ClipboardImage_flatten(ibuf);
  if (ibuf == nullptr) {
    return nullptr;
  }
  const size_t byte_count = GHOST_ClipboardImage_rgba_size(ibuf->x, ibuf->y);
  uint8_t *rgba = static_cast<uint8_t *>(malloc(byte_count));
  if (rgba) {
    memcpy(rgba, ibuf->byte_data(), byte_count);
    *r_width = ibuf->x;
    *r_height = ibuf->y;
  }
  blender::IMB_freeImBuf(ibuf);
  return rgba;
}

blender::ImBuf *GHOST_ClipboardImage_dib_load(const uint8_t *dib, const size_t dib_size)
{
  int width = 0, height = 0;
  if (GHOST_ClipboardImage_dib_decodable(dib, dib_size, &width, &height)) {
    /* Decoded straight into the image's pixels: the only copy of the picture. */
    blender::ImBuf *ibuf = blender::IMB_allocImBuf(
        width, height, blender::ImBufFlags::ByteData | blender::ImBufFlags::UninitializedPixels);
    if (ibuf && !GHOST_ClipboardImage_dib_decode_into(dib, dib_size, ibuf->byte_data_for_write()))
    {
      blender::IMB_freeImBuf(ibuf);
      ibuf = nullptr;
    }
    return ibuf;
  }
  uint8_t file_header[GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES];
  if (!GHOST_ClipboardImage_dib_file_header(dib, dib_size, file_header)) {
    return nullptr;
  }
  /* The BMP reader reads a file: a copy of the bitmap (palette, run-length or 16-bit, so smaller
   * than the picture it decodes to) behind its file header, freed as soon as it is read. */
  const size_t file_size = sizeof(file_header) + dib_size;
  uint8_t *file = static_cast<uint8_t *>(malloc(file_size));
  if (file == nullptr) {
    return nullptr;
  }
  memcpy(file, file_header, sizeof(file_header));
  memcpy(file + sizeof(file_header), dib, dib_size);
  blender::ImBuf *ibuf = blender::IMB_load_image_from_memory(
      file, file_size, blender::ImBufFlags::ByteData, "<clipboard>");
  free(file);
  return GHOST_ClipboardImage_flatten(ibuf);
}
