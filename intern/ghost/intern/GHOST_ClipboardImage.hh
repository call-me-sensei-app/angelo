/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 *
 * Reading a clipboard picture, shared by the platform systems.
 *
 * Clipboard content is untrusted: any application can put any bytes there, with any header. Every
 * function here checks sizes and offsets against the bytes actually available, in 64-bit
 * arithmetic, and refuses (null or false) what it cannot read: it never returns uninitialized
 * memory, a partly read picture or a picture larger than its source.
 *
 * `GHOST_ClipboardImage.cc` (sizes, bitmap rows) and `GHOST_ClipboardImage_DIB.cc` (Windows
 * device-independent bitmaps) are plain C++ without platform headers, compiled and tested on every
 * platform (`GHOST_ClipboardImage_test.cc`, `GHOST_ClipboardImage_bitmap_test.cc`).
 * `GHOST_ClipboardImage_ImBuf.cc` holds what goes through #ImBuf (pictures in a file format, and
 * bitmaps through the BMP reader), tested with the window manager (`wm_clipboard_image_test.cc`);
 * `GHOST_ClipboardImage_Cocoa.mm` draws macOS pictures (`GHOST_ClipboardImage_Cocoa_test.mm`).
 *
 * RGBA results are 8 bits per channel, straight (not premultiplied) alpha, bottom row first, as
 * #GHOST_ISystem::getClipboardImage returns them, allocated with `malloc` (the caller frees).
 */

#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string>

namespace blender {
struct ImBuf;
}

/* -------------------------------------------------------------------- */
/** \name Sizes
 * \{ */

/**
 * Bytes of a tightly packed RGBA picture, or 0 when a side is not 1 to `INT_MAX` (the range of
 * #ImBuf sides) or the bytes do not fit `size_t`.
 */
size_t GHOST_ClipboardImage_rgba_size(int64_t width, int64_t height);

/**
 * Byte offset of row \a row in rows of \a row_bytes each: 64-bit arithmetic, so a picture over
 * 2 GiB never wraps to a negative or short offset. `SIZE_MAX` when either is negative or the
 * offset does not fit `size_t`.
 */
size_t GHOST_ClipboardImage_row_offset(int64_t row, int64_t row_bytes);

/** Bytes of a PNG file that hold its size: the signature and the `IHDR` chunk's start. */
constexpr size_t GHOST_CLIPBOARD_PNG_HEADER_BYTES = 24;

/**
 * The size of a PNG picture from its first #GHOST_CLIPBOARD_PNG_HEADER_BYTES bytes (the
 * signature, then `IHDR`'s width and height): nothing else is read, so a reader of a stream can
 * stop there. False when the bytes are not a PNG's start or a side is not 1 to `INT_MAX`.
 */
bool GHOST_ClipboardImage_png_size(const uint8_t *data,
                                   size_t data_size,
                                   int *r_width,
                                   int *r_height);

/**
 * The clipboard formats Windows' image is read from, in order, given the formats on the clipboard
 * (\a formats, as `EnumClipboardFormats` lists them): the registered "PNG" (\a cf_png) first
 * whenever it is there, as Chromium reads it, since a bitmap's alpha cannot be told straight from
 * premultiplied (Blender and Chromium write straight-alpha PNG beside their bitmaps); then
 * `CF_DIBV5` (\a cf_dibv5, which Windows also makes from `CF_DIB` and `CF_BITMAP`), read when
 * there is no PNG or its data cannot be had or decoded. \return how many of the two are in \a
 * r_order.
 */
size_t GHOST_ClipboardImage_windows_formats(const uint32_t *formats,
                                            size_t count,
                                            uint32_t cf_png,
                                            uint32_t cf_dibv5,
                                            uint32_t r_order[2]);

/**
 * The last clipboard transfer, kept so the checks and the read of one paste share it: the size
 * probe and the read ask for the same selection's bytes, and only the first asks the sending
 * application, which may encode them afresh for every request. Keyed by a selection serial
 * (bumped whenever the selection changes) and the type; #clear frees the bytes.
 */
class GHOST_ClipboardTransfer {
 public:
  GHOST_ClipboardTransfer() = default;
  GHOST_ClipboardTransfer(const GHOST_ClipboardTransfer &) = delete;
  GHOST_ClipboardTransfer &operator=(const GHOST_ClipboardTransfer &) = delete;
  ~GHOST_ClipboardTransfer()
  {
    clear();
  }

  /**
   * The \a type bytes of selection \a selection: from \a fetch the first time (`malloc` bytes,
   * their length in its argument, or null), the kept bytes after. A failed fetch is not kept.
   */
  template<typename Fetch>
  const char *get(const uint64_t selection, const char *type, size_t *r_size, const Fetch &fetch)
  {
    if (data_ == nullptr || selection != selection_ || type_ != type) {
      clear();
      size_t size = 0;
      char *data = fetch(&size);
      if (data == nullptr) {
        return nullptr;
      }
      data_ = data;
      size_ = size;
      selection_ = selection;
      type_ = type;
    }
    *r_size = size_;
    return data_;
  }

  /**
   * Read only from the selection that was current before fetch. A dispatching fetch may replace
   * the selection or detach its owner; the caller must keep this object and serial alive.
   */
  template<typename Fetch>
  const char *get_current(const std::atomic<uint64_t> &selection,
                          const char *type,
                          size_t *r_size,
                          const Fetch &fetch)
  {
    const uint64_t serial = selection.load();
    const char *data = get(serial, type, r_size, fetch);
    if (selection.load() != serial) {
      clear();
      *r_size = 0;
      return nullptr;
    }
    return data;
  }

  /**
   * Discard a previous selection's bytes on the owning thread. The event thread only changes
   * the selection serial; it must not free bytes the size probe or decoder may still be using.
   */
  void clear_if_selection_changed(const uint64_t selection)
  {
    if (data_ != nullptr && selection != selection_) {
      clear();
    }
  }

  /** Free the kept bytes: the read that needed them is done. */
  void clear()
  {
    free(data_);
    data_ = nullptr;
    size_ = 0;
    type_.clear();
  }

 private:
  char *data_ = nullptr;
  size_t size_ = 0;
  uint64_t selection_ = 0;
  std::string type_;
};

/** \} */

/* -------------------------------------------------------------------- */
/** \name Windows Device-Independent Bitmaps (`CF_DIB`, `CF_DIBV5`)
 *
 * \a dib points at a `BITMAPINFOHEADER`, `BITMAPV4HEADER` or `BITMAPV5HEADER`, which the masks,
 * the colour table and the pixels follow; \a dib_size is every byte available there (for
 * clipboard data, `GlobalSize`). Fields are read little-endian from the bytes, never through a
 * cast, so the reader is the same on every platform.
 * \{ */

/**
 * The picture's size from its header alone (the pixels are not read): \a r_height is positive
 * for a top-down bitmap too. False when the header is truncated or a side is invalid. A size is
 * returned for formats #GHOST_ClipboardImage_dib_decode refuses, so a caller can refuse a picture
 * that is too large before reading anything else.
 */
bool GHOST_ClipboardImage_dib_size(const uint8_t *dib,
                                   size_t dib_size,
                                   int *r_width,
                                   int *r_height);

/**
 * Whether #GHOST_ClipboardImage_dib_decode_into reads the bitmap (24-bit and 32-bit `BI_RGB`,
 * 32-bit `BI_BITFIELDS`, bottom-up or top-down) with every byte it needs present, and its size.
 * False for any other bit depth or compression (palettes, 16-bit, `BI_RLE*`, `BI_JPEG`,
 * `BI_PNG`), when the bytes are fewer than the header says (header, masks, colour table and every
 * padded row), or when the pixel data is larger than a DIB can describe (4 GiB).
 */
bool GHOST_ClipboardImage_dib_decodable(const uint8_t *dib,
                                        size_t dib_size,
                                        int *r_width,
                                        int *r_height);

/**
 * Decode a #GHOST_ClipboardImage_dib_decodable bitmap into \a rgba
 * (#GHOST_ClipboardImage_rgba_size bytes for its size). The fourth channel of a 32-bit bitmap
 * (its alpha mask's bits, or the top byte when no mask is given) is read as Chromium reads
 * `CF_DIB`: as premultiplied alpha, divided out, unless some colour is larger than its alpha, in
 * which case the channel is padding or garbage and the picture is opaque. Alpha that is 0 in every
 * pixel is opaque too. False, with \a rgba untouched, when the bitmap is not decodable.
 */
bool GHOST_ClipboardImage_dib_decode_into(const uint8_t *dib, size_t dib_size, uint8_t *rgba);

/**
 * #GHOST_ClipboardImage_dib_decode_into a new `malloc` buffer. Null, with nothing allocated, when
 * the bitmap is not decodable or memory runs out.
 */
uint8_t *GHOST_ClipboardImage_dib_decode(const uint8_t *dib,
                                         size_t dib_size,
                                         int *r_width,
                                         int *r_height);

/** Bytes of a `BITMAPFILEHEADER`. */
constexpr size_t GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES = 14;

/**
 * The `BITMAPFILEHEADER` that makes a bitmap #GHOST_ClipboardImage_dib_decode does not read
 * (1, 4 and 8-bit palettes, `BI_RLE4`, `BI_RLE8`, 16-bit) a BMP file, for Blender's BMP reader
 * (#GHOST_ClipboardImage_dib_load). Its pixel offset counts the header, the masks after a
 * `BITMAPINFOHEADER` (or repeated after a V4/V5 one) and the colour table, all checked to lie
 * within \a dib_size, as are an uncompressed bitmap's rows. False for the formats the decoder
 * reads, for `BI_JPEG`, `BI_PNG` and other compressions, for a header size the BMP reader does not
 * parse (only 40, 52, 56, 108 and 124 bytes), for a top-down run-length bitmap (invalid), for a
 * palette larger than its bit depth allows and for data too short.
 */
bool GHOST_ClipboardImage_dib_file_header(
    const uint8_t *dib,
    size_t dib_size,
    uint8_t r_file_header[GHOST_CLIPBOARD_DIB_FILE_HEADER_BYTES]);

/** \} */

/* -------------------------------------------------------------------- */
/** \name Bitmap Rows (macOS `NSBitmapImageRep`)
 * \{ */

/** How a bitmap's pixels are laid out, as an `NSBitmapImageRep` describes them. */
struct GHOST_ClipboardBitmapLayout {
  /** Sides in pixels (`pixelsWide`, `pixelsHigh`), never in points. */
  int64_t width = 0;
  int64_t height = 0;
  /** Bytes from one row to the next (`bytesPerRow`), padding included. */
  int64_t bytes_per_row = 0;
  int64_t bits_per_pixel = 0;
  int64_t bits_per_sample = 0;
  int64_t samples_per_pixel = 0;
  bool has_alpha = false;
  /** Alpha (or the padding byte of a picture without alpha) comes before the colour. */
  bool alpha_first = false;
  /** Colour is multiplied by alpha (`NSBitmapFormatAlphaNonpremultiplied` not set). */
  bool alpha_premultiplied = false;
  bool float_samples = false;
  bool planar = false;
  /** The colour model is RGB (not grey, CMYK, Lab or indexed). */
  bool rgb = false;
  /** The first row in memory is the top row (true for `NSBitmapImageRep`). */
  bool top_row_first = true;
};

/**
 * Whether #GHOST_ClipboardImage_bitmap_to_rgba reads \a layout: meshed (not planar) RGB with
 * 8-bit integer samples, three without alpha (24 or 32 bits per pixel, the spare byte ignored) or
 * four with alpha first or last, rows at least as long as their pixels, sides 1 to `INT_MAX` and
 * every size within `size_t`. macOS converts every picture into #GHOST_ClipboardImage_draw_layout
 * first, so only that layout is read there; anything else is refused, never misread.
 */
bool GHOST_ClipboardImage_bitmap_readable(const GHOST_ClipboardBitmapLayout &layout);

/**
 * The layout every macOS clipboard picture is converted into before it is read: \a width by
 * \a height 8-bit RGBA in sRGB, alpha last, \a premultiplied or straight, top row first, with an
 * explicit, tight `bytes_per_row` of `width * 4`, so its bytes are #GHOST_ClipboardImage_rgba_size
 * and can be converted in place. False, before anything is allocated, when a side is not 1 to
 * `INT_MAX` or the bytes do not fit `size_t`.
 */
bool GHOST_ClipboardImage_draw_layout(int64_t width,
                                      int64_t height,
                                      bool premultiplied,
                                      GHOST_ClipboardBitmapLayout &r_layout);

/**
 * Copy \a layout's rows from \a pixels (`bytes_per_row * height` bytes) into \a rgba
 * (#GHOST_ClipboardImage_rgba_size bytes): bottom row first, each row read at its own stride,
 * premultiplied colour divided by alpha, opaque alpha for a picture without one. \a pixels may be
 * \a rgba itself (converted in place) when its rows are tight four-byte pixels, as a
 * #GHOST_ClipboardImage_draw_layout drawing is; other buffers must not overlap. False, with
 * \a rgba untouched, when the layout is not readable or cannot be converted in place.
 */
bool GHOST_ClipboardImage_bitmap_to_rgba(const GHOST_ClipboardBitmapLayout &layout,
                                         const uint8_t *pixels,
                                         uint8_t *rgba);

/** \} */

/* -------------------------------------------------------------------- */
/** \name Pictures in a File Format (#ImBuf, `GHOST_ClipboardImage_ImBuf.cc`)
 * \{ */

/**
 * The size of the picture in the file at \a filepath, from its header alone (#ImBufFlags::Test,
 * nothing decoded). False when no reader recognizes the file.
 */
bool GHOST_ClipboardImage_file_size(const char *filepath, int *r_width, int *r_height);

/** As #GHOST_ClipboardImage_file_size, for a file's bytes in memory. */
bool GHOST_ClipboardImage_memory_size(const uint8_t *data,
                                      size_t data_size,
                                      int *r_width,
                                      int *r_height);

/**
 * Load the picture in the file at \a filepath for the clipboard: flat (never as an OpenEXR
 * multilayer file, whose passes have no single picture), byte pixels preferred.
 */
blender::ImBuf *GHOST_ClipboardImage_file_load(const char *filepath);

/**
 * Make \a ibuf (null is allowed) the clipboard's image: byte pixels only, in place. An OpenEXR
 * multilayer handle is closed (its decoded passes would leak), float-only pixels (a 16-bit PNG or
 * TIFF, an OpenEXR or Radiance HDR file) are converted to bytes and the float pixels freed.
 * \return \a ibuf, or null (with \a ibuf freed) when it has no pixels or memory runs out.
 */
blender::ImBuf *GHOST_ClipboardImage_flatten(blender::ImBuf *ibuf);

/**
 * A copy of \a ibuf's picture as `malloc` RGBA bytes, for #GHOST_ISystem::getClipboardImage,
 * with \a ibuf flattened and freed (null is allowed). Null when there are no pixels or memory
 * runs out.
 */
uint8_t *GHOST_ClipboardImage_rgba_from_imbuf(blender::ImBuf *ibuf, int *r_width, int *r_height);

/**
 * Read a device-independent bitmap of any format Windows puts on the clipboard into a new #ImBuf:
 * those #GHOST_ClipboardImage_dib_decode_into reads straight into the image's pixels, and the
 * others (1, 4 and 8-bit palettes, `BI_RLE4`, `BI_RLE8`, 16-bit) through Blender's BMP reader
 * behind a #GHOST_ClipboardImage_dib_file_header (on a copy of the bitmap, freed before this
 * returns), which reads only within the bytes it is given. Null when neither reads it.
 */
blender::ImBuf *GHOST_ClipboardImage_dib_load(const uint8_t *dib, size_t dib_size);

/** \} */
