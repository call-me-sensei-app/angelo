/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup GHOST
 *
 * A macOS picture read as RGBA bytes (see `GHOST_ClipboardImage_Cocoa.hh`).
 */

#include "GHOST_ClipboardImage_Cocoa.hh"

#include <cstdlib>

#import <Accelerate/Accelerate.h>
#import <Cocoa/Cocoa.h>
#import <ImageIO/ImageIO.h>

/* -------------------------------------------------------------------- */
/** \name Pictures
 * \{ */

/** The first bitmap representation of \a image: the one its pixels are read from, or nil. */
static NSBitmapImageRep *getNSImageBitmap(NSImage *image)
{
  for (NSImageRep *representation in [image representations]) {
    if ([representation isKindOfClass:[NSBitmapImageRep class]]) {
      return (NSBitmapImageRep *)representation;
    }
  }
  return nil;
}

CGImageRef GHOST_ClipboardImage_NSImage_drawing(NSImage *image,
                                                int64_t *r_width,
                                                int64_t *r_height)
{
  NSBitmapImageRep *bitmap = getNSImageBitmap(image);
  CGImageRef cg_image = bitmap ? bitmap.CGImage : nullptr;
  if (cg_image == nullptr) {
    return nullptr;
  }
  const int64_t width = int64_t(CGImageGetWidth(cg_image));
  const int64_t height = int64_t(CGImageGetHeight(cg_image));
  if (GHOST_ClipboardImage_rgba_size(width, height) == 0) {
    return nullptr;
  }
  *r_width = width;
  *r_height = height;
  return cg_image;
}

/** vImage: straight 8-bit RGBA in \a srgb, into \a layout's rows. */
static bool convertCGImage(CGImageRef image,
                           const GHOST_ClipboardBitmapLayout &layout,
                           CGColorSpaceRef srgb,
                           uint8_t *rgba)
{
  vImage_CGImageFormat format = {};
  format.bitsPerComponent = 8;
  format.bitsPerPixel = 32;
  format.colorSpace = srgb;
  format.bitmapInfo = CGBitmapInfo(kCGImageAlphaLast) | CGBitmapInfo(kCGBitmapByteOrderDefault);
  format.renderingIntent = kCGRenderingIntentDefault;
  vImage_Buffer buffer = {};
  buffer.data = rgba;
  buffer.width = vImagePixelCount(layout.width);
  buffer.height = vImagePixelCount(layout.height);
  buffer.rowBytes = size_t(layout.bytes_per_row);
  /* Into the caller's buffer: vImage allocates no pixels of its own. */
  return vImageBuffer_InitWithCGImage(&buffer, &format, nullptr, image, kvImageNoAllocate) ==
         kvImageNoError;
}

/** Core Graphics: premultiplied 8-bit RGBA in \a srgb, into \a layout's rows. */
static bool drawCGImage(CGImageRef image,
                        const GHOST_ClipboardBitmapLayout &layout,
                        CGColorSpaceRef srgb,
                        uint8_t *rgba)
{
  CGContextRef context = CGBitmapContextCreate(rgba,
                                               size_t(layout.width),
                                               size_t(layout.height),
                                               size_t(layout.bits_per_sample),
                                               size_t(layout.bytes_per_row),
                                               srgb,
                                               CGBitmapInfo(kCGImageAlphaPremultipliedLast) |
                                                   CGBitmapInfo(kCGBitmapByteOrder32Big));
  if (context == nullptr) {
    return false;
  }
  /* Replace every pixel (the buffer is not cleared first), sample for sample. */
  CGContextSetBlendMode(context, kCGBlendModeCopy);
  CGContextSetInterpolationQuality(context, kCGInterpolationNone);
  CGContextDrawImage(
      context, CGRectMake(0, 0, CGFloat(layout.width), CGFloat(layout.height)), image);
  CGContextRelease(context);
  return true;
}

bool GHOST_ClipboardImage_draw(CGImageRef image,
                               const int64_t width,
                               const int64_t height,
                               uint8_t *rgba)
{
  GHOST_ClipboardBitmapLayout straight, premultiplied;
  if (image == nullptr || rgba == nullptr ||
      !GHOST_ClipboardImage_draw_layout(width, height, false, straight) ||
      !GHOST_ClipboardImage_draw_layout(width, height, true, premultiplied))
  {
    return false;
  }
  CGColorSpaceRef srgb = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
  if (srgb == nullptr) {
    return false;
  }
  const GHOST_ClipboardBitmapLayout *layout = nullptr;
  if (convertCGImage(image, straight, srgb, rgba)) {
    layout = &straight;
  }
  else if (drawCGImage(image, premultiplied, srgb, rgba)) {
    layout = &premultiplied;
  }
  CGColorSpaceRelease(srgb);
  /* Top row first to bottom row first (and premultiplied colour divided out), in place. */
  return layout && GHOST_ClipboardImage_bitmap_to_rgba(*layout, rgba, rgba);
}

uint8_t *GHOST_ClipboardImage_from_NSImage(NSImage *image, int *r_width, int *r_height)
{
  @autoreleasepool {
    int64_t width = 0, height = 0;
    CGImageRef cg_image = GHOST_ClipboardImage_NSImage_drawing(image, &width, &height);
    if (cg_image == nullptr) {
      return nullptr;
    }
    uint8_t *rgba = static_cast<uint8_t *>(malloc(GHOST_ClipboardImage_rgba_size(width, height)));
    if (rgba == nullptr) {
      return nullptr;
    }
    if (!GHOST_ClipboardImage_draw(cg_image, width, height, rgba)) {
      free(rgba);
      return nullptr;
    }
    *r_width = int(width);
    *r_height = int(height);
    return rgba;
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Pasteboard
 * \{ */

/** Pasteboard data read as a picture, in order: PNG (the smaller transfer), then TIFF. */
static NSArray<NSPasteboardType> *pasteboardImageDataTypes()
{
  return @[ NSPasteboardTypePNG, NSPasteboardTypeTIFF ];
}

/**
 * The image file copied to \a pasteboard (Finder), or nil: a file of a type ImageIO reads, the
 * reader the size probe and the read use (`NSImage` also opens PDF, EPS and SVG files, which have
 * no bitmap to paste).
 */
static NSURL *pasteboardImageFile(NSPasteboard *pasteboard)
{
  NSArray *image_types = [(NSArray *)CGImageSourceCopyTypeIdentifiers() autorelease];
  NSDictionary *options = @{
    NSPasteboardURLReadingFileURLsOnlyKey : @YES,
    NSPasteboardURLReadingContentsConformToTypesKey : image_types
  };
  NSArray *matches = [pasteboard readObjectsForClasses:@[ [NSURL class] ] options:options];
  return matches.count ? (NSURL *)matches.firstObject : nil;
}

/** \a pasteboard's PNG or TIFF data, or nil. */
static NSData *pasteboardImageData(NSPasteboard *pasteboard)
{
  NSPasteboardType type = [pasteboard availableTypeFromArray:pasteboardImageDataTypes()];
  return type ? [pasteboard dataForType:type] : nil;
}

bool GHOST_ClipboardImage_pasteboard_has_image(NSPasteboard *pasteboard)
{
  @autoreleasepool {
    if ([pasteboard availableTypeFromArray:@[ NSPasteboardTypeFileURL ]]) {
      /* A copied file: a picture only when it is an image file. */
      return pasteboardImageFile(pasteboard) != nil;
    }
    return [pasteboard availableTypeFromArray:pasteboardImageDataTypes()] != nil;
  }
}

/** The pixel size of the first image in \a source (released here), from its properties. */
static bool getCGImageSourceSize(CGImageSourceRef source, int *r_width, int *r_height)
{
  if (source == nullptr) {
    return false;
  }
  bool found = false;
  if (CGImageSourceGetCount(source) > 0) {
    if (CFDictionaryRef properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nullptr)) {
      const void *width_value = CFDictionaryGetValue(properties, kCGImagePropertyPixelWidth);
      const void *height_value = CFDictionaryGetValue(properties, kCGImagePropertyPixelHeight);
      int64_t width = 0, height = 0;
      if (width_value && height_value && CFGetTypeID(width_value) == CFNumberGetTypeID() &&
          CFGetTypeID(height_value) == CFNumberGetTypeID() &&
          CFNumberGetValue(static_cast<CFNumberRef>(width_value), kCFNumberSInt64Type, &width) &&
          CFNumberGetValue(static_cast<CFNumberRef>(height_value), kCFNumberSInt64Type, &height) &&
          GHOST_ClipboardImage_rgba_size(width, height) != 0)
      {
        *r_width = int(width);
        *r_height = int(height);
        found = true;
      }
      CFRelease(properties);
    }
  }
  CFRelease(source);
  return found;
}

/**
 * The ImageIO source of \a pasteboard's picture (+1, the caller releases it), shared by the size
 * probe and the read: the copied image file, else the PNG or TIFF data. Null when there is none.
 * \a r_from_data tells which.
 */
static CGImageSourceRef pasteboardImageSource(NSPasteboard *pasteboard, bool *r_from_data)
{
  *r_from_data = false;
  if (!GHOST_ClipboardImage_pasteboard_has_image(pasteboard)) {
    return nullptr;
  }
  if (NSURL *file = pasteboardImageFile(pasteboard)) {
    return CGImageSourceCreateWithURL((__bridge CFURLRef)file, nullptr);
  }
  if (NSData *data = pasteboardImageData(pasteboard)) {
    *r_from_data = true;
    return CGImageSourceCreateWithData((__bridge CFDataRef)data, nullptr);
  }
  return nullptr;
}

bool GHOST_ClipboardImage_pasteboard_size(NSPasteboard *pasteboard, int *r_width, int *r_height)
{
  @autoreleasepool {
    bool from_data;
    return getCGImageSourceSize(pasteboardImageSource(pasteboard, &from_data), r_width, r_height);
  }
}

NSImage *GHOST_ClipboardImage_pasteboard_image(NSPasteboard *pasteboard)
{
  bool from_data = false;
  CGImageSourceRef source = pasteboardImageSource(pasteboard, &from_data);
  CGImageRef cg_image = nullptr;
  if (source) {
    if (CGImageSourceGetCount(source) > 0) {
      cg_image = CGImageSourceCreateImageAtIndex(source, 0, nullptr);
    }
    CFRelease(source);
  }
  if (cg_image) {
    /* The picture the size probe measured, as a bitmap. */
    NSBitmapImageRep *bitmap = [[[NSBitmapImageRep alloc] initWithCGImage:cg_image] autorelease];
    CGImageRelease(cg_image);
    NSImage *image = [[[NSImage alloc]
        initWithSize:NSMakeSize(CGFloat(bitmap.pixelsWide), CGFloat(bitmap.pixelsHigh))]
        autorelease];
    [image addRepresentation:bitmap];
    return image;
  }
  /* PNG or TIFF bytes ImageIO could not decode: one retry with whatever `NSImage` makes of the
   * pasteboard. Never for a copied file, whose pasteboard also carries the file's icon. */
  return from_data ? [[[NSImage alloc] initWithPasteboard:pasteboard] autorelease] : nil;
}

/** \} */
