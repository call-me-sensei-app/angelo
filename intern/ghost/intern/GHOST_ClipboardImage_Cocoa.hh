/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 *
 * A macOS clipboard or dropped picture read as RGBA bytes (`GHOST_ClipboardImage_Cocoa.mm`,
 * tested by `GHOST_ClipboardImage_Cocoa_test.mm`).
 *
 * The pasteboard's picture is read by ImageIO from one source that the size probe reads too: a
 * copied file of a type ImageIO reads, else the pasteboard's PNG or TIFF data (in that order).
 * Applications such as Keynote and Pages declare PDF first; their picture is read from the PNG or
 * TIFF beside it. `NSImage`'s reading of the whole pasteboard is a retry only when that PNG or
 * TIFF data does not decode.
 *
 * The picture's first bitmap is converted, whatever its format, byte order and colour profile,
 * by vImage into 8-bit straight (not premultiplied) RGBA in sRGB laid out as
 * #GHOST_ClipboardImage_draw_layout; only that buffer is read, never the bitmap's own layout,
 * which changes when a `CGImage`-backed bitmap is unpacked. Should vImage fail, Core Graphics
 * draws the picture into a premultiplied context instead (translucent colour then loses
 * precision, see #GHOST_ClipboardImage_draw).
 */

#pragma once

#include <CoreGraphics/CoreGraphics.h>

#include "GHOST_ClipboardImage.hh"

#ifdef __OBJC__
@class NSImage;
@class NSPasteboard;

/**
 * The first bitmap of \a image as a Core Graphics image (owned by the bitmap, valid while
 * \a image is), and its pixel size, checked (#GHOST_ClipboardImage_rgba_size) before anything is
 * allocated. Null when there is no bitmap or the size is invalid.
 */
CGImageRef GHOST_ClipboardImage_NSImage_drawing(NSImage *image,
                                                int64_t *r_width,
                                                int64_t *r_height);

/**
 * Copy \a image's picture into newly allocated (`malloc`) RGBA bytes, straight alpha, sRGB,
 * bottom row first. Null when it has no bitmap, it cannot be converted or memory runs out.
 */
uint8_t *GHOST_ClipboardImage_from_NSImage(NSImage *image, int *r_width, int *r_height);

/**
 * Whether \a pasteboard holds a picture #GHOST_ClipboardImage_pasteboard_image reads: a copied
 * file of a type ImageIO reads (not PDF, EPS or SVG, nor any other file with its icon beside it),
 * or PNG or TIFF data.
 */
bool GHOST_ClipboardImage_pasteboard_has_image(NSPasteboard *pasteboard);

/**
 * The size of \a pasteboard's picture from its header alone (ImageIO properties): the copied
 * image file's, or the PNG or TIFF data's that #GHOST_ClipboardImage_pasteboard_image reads.
 */
bool GHOST_ClipboardImage_pasteboard_size(NSPasteboard *pasteboard, int *r_width, int *r_height);

/**
 * \a pasteboard's picture (autoreleased) as one bitmap, read by ImageIO from the source the size
 * probe reads; nil when there is none or a copied file does not decode.
 */
NSImage *GHOST_ClipboardImage_pasteboard_image(NSPasteboard *pasteboard);
#endif

/**
 * Convert \a image into \a rgba (#GHOST_ClipboardImage_rgba_size bytes for \a width by
 * \a height): straight RGBA in sRGB, bottom row first. The conversion writes into \a rgba; its
 * source is the bitmap's decoded pixels, which the bitmap holds.
 *
 * vImage converts to straight alpha, so translucent colour keeps the precision the picture has.
 * When vImage cannot convert the picture, it is drawn into a premultiplied context and divided
 * back: a premultiplied 8-bit channel holds only `a + 1` levels at alpha `a`, so a colour comes
 * back within one level, `255 / a`, of its value (within 26 at alpha 10, exact when opaque).
 * False when neither can convert it.
 */
bool GHOST_ClipboardImage_draw(CGImageRef image, int64_t width, int64_t height, uint8_t *rgba);
