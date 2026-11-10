/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * A macOS picture read as RGBA (`GHOST_ClipboardImage_Cocoa.mm`), on real bitmaps of the
 * formats applications put on the pasteboard: 8-bit RGBA as Blender's own Copy Image writes it,
 * grey, 16-bit with translucency, a `CGImage`-backed bitmap in BGRA byte order with alpha first,
 * Display P3 (converted to sRGB like everything else) and translucent colour (kept straight). And
 * the pasteboard read on private pasteboards, never the person's clipboard: PDF declared before
 * PNG (as Keynote and Pages do), PDF alone, an image file and a copied file that is not one.
 */

#include "testing/testing.h"

#include <cstdlib>
#include <cstring>

#import <Cocoa/Cocoa.h>

#include "GHOST_ClipboardImage_Cocoa.hh"

namespace ghost_clipboard_image::tests {

/** A 2 x 2 bitmap; \a fill writes its pixel bytes, top row first. */
template<typename Fill>
static NSImage *picture(const int bits_per_sample,
                        const int samples,
                        const bool alpha,
                        NSColorSpaceName space,
                        const NSBitmapFormat format,
                        const Fill &fill)
{
  NSBitmapImageRep *bitmap = [[[NSBitmapImageRep alloc]
      initWithBitmapDataPlanes:nil
                    pixelsWide:2
                    pixelsHigh:2
                 bitsPerSample:bits_per_sample
               samplesPerPixel:samples
                      hasAlpha:alpha
                      isPlanar:NO
                colorSpaceName:space
                  bitmapFormat:format
                   bytesPerRow:0
                  bitsPerPixel:bits_per_sample * samples] autorelease];
  fill(bitmap.bitmapData, bitmap.bytesPerRow);
  NSImage *image = [[[NSImage alloc] initWithSize:NSMakeSize(2, 2)] autorelease];
  [image addRepresentation:bitmap];
  return image;
}

static NSImage *image_of(NSBitmapImageRep *bitmap)
{
  NSImage *image = [[[NSImage alloc] initWithSize:bitmap.size] autorelease];
  [image addRepresentation:bitmap];
  return image;
}

/** Read \a image; the result is freed by the caller. */
static uint8_t *read(NSImage *image)
{
  int width = 0, height = 0;
  uint8_t *rgba = GHOST_ClipboardImage_from_NSImage(image, &width, &height);
  if (rgba) {
    EXPECT_EQ(width, 2);
    EXPECT_EQ(height, 2);
  }
  return rgba;
}

TEST(GHOST_ClipboardImageCocoa, BlendersOwnCopyKeepsItsValues)
{
  /* As `putClipboardImage` builds it: 8-bit RGBA, device RGB, straight alpha. */
  @autoreleasepool {
    const uint8_t top[8] = {10, 20, 30, 255, 200, 100, 50, 255};
    const uint8_t bottom[8] = {1, 2, 3, 255, 250, 251, 252, 255};
    NSImage *image = picture(8,
                             4,
                             true,
                             NSDeviceRGBColorSpace,
                             NSBitmapFormatAlphaNonpremultiplied,
                             [&](uint8_t *bytes, NSInteger row) {
                               memcpy(bytes, top, 8);
                               memcpy(bytes + row, bottom, 8);
                             });
    uint8_t *rgba = read(image);
    ASSERT_NE(rgba, nullptr);
    for (int i = 0; i < 8; i++) {
      EXPECT_NEAR(rgba[i], bottom[i], 1) << "bottom row first, byte " << i;
      EXPECT_NEAR(rgba[8 + i], top[i], 1) << "top row, byte " << i;
    }
    free(rgba);
  }
}

TEST(GHOST_ClipboardImageCocoa, GreyAndSixteenBitAreRead)
{
  @autoreleasepool {
    NSImage *grey = picture(
        8, 1, false, NSDeviceWhiteColorSpace, 0, [](uint8_t *bytes, NSInteger row) {
          bytes[0] = 10;
          bytes[1] = 200;
          bytes[row] = 100;
          bytes[row + 1] = 50;
        });
    uint8_t *rgba = read(grey);
    ASSERT_NE(rgba, nullptr) << "grey is drawn, never refused";
    EXPECT_NEAR(rgba[0], 100, 1);
    EXPECT_EQ(rgba[0], rgba[1]);
    EXPECT_EQ(rgba[1], rgba[2]);
    EXPECT_EQ(rgba[3], 255);
    EXPECT_NEAR(rgba[8], 10, 1);
    free(rgba);

    /* 16-bit red, opaque on top and half transparent below: straight alpha out. */
    NSImage *deep = picture(16,
                            4,
                            true,
                            NSDeviceRGBColorSpace,
                            NSBitmapFormatAlphaNonpremultiplied,
                            [](uint8_t *bytes, NSInteger row) {
                              for (int y = 0; y < 2; y++) {
                                uint16_t *pixels = reinterpret_cast<uint16_t *>(bytes + y * row);
                                for (int x = 0; x < 2; x++) {
                                  pixels[x * 4 + 0] = 65535;
                                  pixels[x * 4 + 1] = 0;
                                  pixels[x * 4 + 2] = 0;
                                  pixels[x * 4 + 3] = y == 0 ? 65535 : 32768;
                                }
                              }
                            });
    rgba = read(deep);
    ASSERT_NE(rgba, nullptr);
    EXPECT_GE(rgba[0], 250) << "straight red, not darkened by alpha";
    EXPECT_NEAR(rgba[3], 128, 1);
    EXPECT_EQ(rgba[11], 255);
    free(rgba);
  }
}

TEST(GHOST_ClipboardImageCocoa, ByteOrderAndAlphaFirstAreReadByTheConversion)
{
  /* A bitmap backed by a `CGImage` in BGRA order with alpha first (little-endian 32-bit pixels),
   * as screenshots and many applications produce: red stays red, green stays green. */
  @autoreleasepool {
    CGColorSpaceRef srgb = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    uint8_t bgra[16] = {0, 0, 255, 255, 0, 255, 0, 255, 0, 0, 255, 255, 0, 255, 0, 255};
    CGContextRef context = CGBitmapContextCreate(bgra,
                                                 2,
                                                 2,
                                                 8,
                                                 8,
                                                 srgb,
                                                 CGBitmapInfo(kCGImageAlphaPremultipliedFirst) |
                                                     CGBitmapInfo(kCGBitmapByteOrder32Little));
    ASSERT_NE(context, nullptr);
    CGImageRef cg_image = CGBitmapContextCreateImage(context);
    NSBitmapImageRep *bitmap = [[[NSBitmapImageRep alloc] initWithCGImage:cg_image] autorelease];
    CGImageRelease(cg_image);
    CGContextRelease(context);
    CGColorSpaceRelease(srgb);

    uint8_t *rgba = read(image_of(bitmap));
    ASSERT_NE(rgba, nullptr);
    EXPECT_EQ(rgba[0], 255) << "red";
    EXPECT_EQ(rgba[1], 0);
    EXPECT_EQ(rgba[2], 0);
    EXPECT_EQ(rgba[3], 255);
    EXPECT_EQ(rgba[4], 0) << "green";
    EXPECT_EQ(rgba[5], 255);
    EXPECT_EQ(rgba[6], 0);
    free(rgba);
  }
}

TEST(GHOST_ClipboardImageCocoa, DisplayP3IsConvertedToSRGB)
{
  @autoreleasepool {
    NSImage *device = picture(8,
                              4,
                              true,
                              NSDeviceRGBColorSpace,
                              NSBitmapFormatAlphaNonpremultiplied,
                              [](uint8_t *bytes, NSInteger row) {
                                for (int y = 0; y < 2; y++) {
                                  for (int x = 0; x < 2; x++) {
                                    uint8_t *pixel = bytes + y * row + x * 4;
                                    pixel[0] = 200;
                                    pixel[1] = 100;
                                    pixel[2] = 50;
                                    pixel[3] = 255;
                                  }
                                }
                              });
    NSBitmapImageRep *p3 = [(NSBitmapImageRep *)device.representations.firstObject
        bitmapImageRepByRetaggingWithColorSpace:[NSColorSpace displayP3ColorSpace]];
    ASSERT_NE(p3, nil);
    NSColor *expected = [[NSColor colorWithDisplayP3Red:200 / 255.0
                                                  green:100 / 255.0
                                                   blue:50 / 255.0
                                                  alpha:1.0]
        colorUsingColorSpace:[NSColorSpace sRGBColorSpace]];
    uint8_t *rgba = read(image_of(p3));
    ASSERT_NE(rgba, nullptr);
    EXPECT_NEAR(rgba[0], expected.redComponent * 255.0, 2);
    EXPECT_NEAR(rgba[1], expected.greenComponent * 255.0, 2);
    EXPECT_NEAR(rgba[2], expected.blueComponent * 255.0, 2);
    EXPECT_TRUE(std::abs(rgba[0] - 200) > 2 || std::abs(rgba[1] - 100) > 2 ||
                std::abs(rgba[2] - 50) > 2)
        << "converted, not copied raw";
    free(rgba);
  }
}

TEST(GHOST_ClipboardImageCocoa, NoBitmapIsNothing)
{
  @autoreleasepool {
    NSImage *empty = [[[NSImage alloc] initWithSize:NSMakeSize(2, 2)] autorelease];
    int width = -1, height = -1;
    EXPECT_EQ(GHOST_ClipboardImage_from_NSImage(empty, &width, &height), nullptr);
    EXPECT_EQ(width, -1);
    EXPECT_EQ(GHOST_ClipboardImage_from_NSImage(nil, &width, &height), nullptr);
  }
}

TEST(GHOST_ClipboardImageCocoa, TranslucentColourKeepsItsPrecision)
{
  /* Straight alpha in, straight alpha out: alpha 10 holds only 11 premultiplied levels, so a
   * premultiplied round trip would bring 123 back as 128. */
  @autoreleasepool {
    NSImage *image = picture(8,
                             4,
                             true,
                             NSDeviceRGBColorSpace,
                             NSBitmapFormatAlphaNonpremultiplied,
                             [](uint8_t *bytes, NSInteger row) {
                               for (int y = 0; y < 2; y++) {
                                 for (int x = 0; x < 2; x++) {
                                   uint8_t *pixel = bytes + y * row + x * 4;
                                   pixel[0] = 123;
                                   pixel[1] = 200;
                                   pixel[2] = 7;
                                   pixel[3] = 10;
                                 }
                               }
                             });
    uint8_t *rgba = read(image);
    ASSERT_NE(rgba, nullptr);
    EXPECT_NEAR(rgba[0], 123, 1);
    EXPECT_NEAR(rgba[1], 200, 1);
    EXPECT_NEAR(rgba[2], 7, 1);
    EXPECT_EQ(rgba[3], 10);
    free(rgba);

    /* A premultiplied source has only the precision it stores: 5 at alpha 10 is 127.5. */
    NSImage *premultiplied = picture(
        8, 4, true, NSDeviceRGBColorSpace, 0, [](uint8_t *bytes, NSInteger row) {
          for (int y = 0; y < 2; y++) {
            for (int x = 0; x < 2; x++) {
              uint8_t *pixel = bytes + y * row + x * 4;
              pixel[0] = pixel[1] = pixel[2] = 5;
              pixel[3] = 10;
            }
          }
        });
    rgba = read(premultiplied);
    ASSERT_NE(rgba, nullptr);
    EXPECT_NEAR(rgba[0], 127.5, 1);
    EXPECT_EQ(rgba[3], 10);
    free(rgba);
  }
}

/** A private pasteboard, never the person's clipboard; released with the scope. */
struct PrivatePasteboard {
  NSPasteboard *pasteboard = [NSPasteboard pasteboardWithUniqueName];
  ~PrivatePasteboard()
  {
    [pasteboard releaseGlobally];
  }
};

static NSData *png_of_value(const uint8_t value)
{
  NSBitmapImageRep *bitmap = [[[NSBitmapImageRep alloc]
      initWithBitmapDataPlanes:nil
                    pixelsWide:8
                    pixelsHigh:4
                 bitsPerSample:8
               samplesPerPixel:4
                      hasAlpha:YES
                      isPlanar:NO
                colorSpaceName:NSDeviceRGBColorSpace
                   bytesPerRow:0
                  bitsPerPixel:32] autorelease];
  /* Opaque grey \a value (the bitmap stores premultiplied colour, equal to it when opaque). */
  for (NSInteger y = 0; y < 4; y++) {
    uint8_t *row = bitmap.bitmapData + y * bitmap.bytesPerRow;
    for (int x = 0; x < 8; x++) {
      row[x * 4 + 0] = row[x * 4 + 1] = row[x * 4 + 2] = value;
      row[x * 4 + 3] = 255;
    }
  }
  return [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
}

static NSData *pdf_page()
{
  NSView *view = [[[NSView alloc] initWithFrame:NSMakeRect(0, 0, 8, 8)] autorelease];
  return [view dataWithPDFInsideRect:NSMakeRect(0, 0, 8, 8)];
}

TEST(GHOST_ClipboardImageCocoa, PngIsReadWhenPdfIsDeclaredFirst)
{
  @autoreleasepool {
    PrivatePasteboard board;
    [board.pasteboard declareTypes:@[ NSPasteboardTypePDF, NSPasteboardTypePNG ] owner:nil];
    [board.pasteboard setData:pdf_page() forType:NSPasteboardTypePDF];
    [board.pasteboard setData:png_of_value(200) forType:NSPasteboardTypePNG];

    EXPECT_TRUE(GHOST_ClipboardImage_pasteboard_has_image(board.pasteboard));
    int width = 0, height = 0;
    ASSERT_TRUE(GHOST_ClipboardImage_pasteboard_size(board.pasteboard, &width, &height));
    EXPECT_EQ(width, 8);
    EXPECT_EQ(height, 4);
    NSImage *image = GHOST_ClipboardImage_pasteboard_image(board.pasteboard);
    ASSERT_NE(image, nil);
    width = height = 0;
    uint8_t *rgba = GHOST_ClipboardImage_from_NSImage(image, &width, &height);
    ASSERT_NE(rgba, nullptr) << "the PNG, not the PDF without a bitmap";
    EXPECT_EQ(width, 8) << "the size the probe read";
    EXPECT_EQ(height, 4);
    EXPECT_NEAR(rgba[0], 200, 1);
    free(rgba);
  }
}

TEST(GHOST_ClipboardImageCocoa, PdfAloneOrANonImageFileIsNoPicture)
{
  @autoreleasepool {
    PrivatePasteboard pdf;
    [pdf.pasteboard declareTypes:@[ NSPasteboardTypePDF ] owner:nil];
    [pdf.pasteboard setData:pdf_page() forType:NSPasteboardTypePDF];
    EXPECT_FALSE(GHOST_ClipboardImage_pasteboard_has_image(pdf.pasteboard));
    EXPECT_EQ(GHOST_ClipboardImage_pasteboard_image(pdf.pasteboard), nil);
    int width = -1, height = -1;
    EXPECT_FALSE(GHOST_ClipboardImage_pasteboard_size(pdf.pasteboard, &width, &height));

    /* A copied text file, with an icon beside it as Finder puts one: not a picture. */
    NSString *path = [NSTemporaryDirectory()
        stringByAppendingPathComponent:[[NSUUID UUID].UUIDString stringByAppendingString:@".txt"]];
    [@"text" writeToFile:path atomically:YES encoding:NSUTF8StringEncoding error:nil];
    PrivatePasteboard file;
    [file.pasteboard clearContents];
    [file.pasteboard writeObjects:@[ [NSURL fileURLWithPath:path] ]];
    [file.pasteboard addTypes:@[ NSPasteboardTypePNG ] owner:nil];
    [file.pasteboard setData:png_of_value(9) forType:NSPasteboardTypePNG];
    EXPECT_FALSE(GHOST_ClipboardImage_pasteboard_has_image(file.pasteboard));
    EXPECT_EQ(GHOST_ClipboardImage_pasteboard_image(file.pasteboard), nil);
    [[NSFileManager defaultManager] removeItemAtPath:path error:nil];
    EXPECT_EQ(width, -1);
  }
}

TEST(GHOST_ClipboardImageCocoa, ACopiedPdfFileIsNoPicture)
{
  /* `NSImage` opens PDF (and EPS, SVG) files, without a bitmap to paste: a copied PDF file is no
   * picture to the check, the size probe or the read alike. */
  @autoreleasepool {
    NSString *path = [NSTemporaryDirectory()
        stringByAppendingPathComponent:[[NSUUID UUID].UUIDString stringByAppendingString:@".pdf"]];
    ASSERT_TRUE([pdf_page() writeToFile:path atomically:YES]);
    PrivatePasteboard board;
    [board.pasteboard clearContents];
    [board.pasteboard writeObjects:@[ [NSURL fileURLWithPath:path] ]];
    EXPECT_FALSE(GHOST_ClipboardImage_pasteboard_has_image(board.pasteboard));
    int width = -1, height = -1;
    EXPECT_FALSE(GHOST_ClipboardImage_pasteboard_size(board.pasteboard, &width, &height));
    EXPECT_EQ(GHOST_ClipboardImage_pasteboard_image(board.pasteboard), nil);
    [[NSFileManager defaultManager] removeItemAtPath:path error:nil];
  }
}

TEST(GHOST_ClipboardImageCocoa, ACopiedImageFileIsRead)
{
  @autoreleasepool {
    NSString *path = [NSTemporaryDirectory()
        stringByAppendingPathComponent:[[NSUUID UUID].UUIDString stringByAppendingString:@".png"]];
    ASSERT_TRUE([png_of_value(77) writeToFile:path atomically:YES]);
    PrivatePasteboard board;
    [board.pasteboard clearContents];
    [board.pasteboard writeObjects:@[ [NSURL fileURLWithPath:path] ]];
    EXPECT_TRUE(GHOST_ClipboardImage_pasteboard_has_image(board.pasteboard));
    int width = 0, height = 0;
    EXPECT_TRUE(GHOST_ClipboardImage_pasteboard_size(board.pasteboard, &width, &height));
    EXPECT_EQ(width, 8);
    uint8_t *rgba = GHOST_ClipboardImage_from_NSImage(
        GHOST_ClipboardImage_pasteboard_image(board.pasteboard), &width, &height);
    ASSERT_NE(rgba, nullptr);
    EXPECT_NEAR(rgba[0], 77, 1);
    free(rgba);
    [[NSFileManager defaultManager] removeItemAtPath:path error:nil];
  }
}

}  // namespace ghost_clipboard_image::tests
