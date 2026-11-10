/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The PNG a pasted clipboard picture becomes (`angelo_clipboard_write_png`).
 *
 * Reading the clipboard itself needs a window system (GHOST), which a GTest does not have; the
 * GUI suite `test_blender_chat_paste_image` covers it in the running app, and the BACKGROUND
 * suite `test_blender_clipboard_binding` the Python argument handling. Everything after the read
 * is here: the picture written is the clipboard's pixels, opaque pictures lose only their
 * (all-opaque) alpha channel, translucent ones keep it, a float-only picture is converted, the
 * side limit refuses before any encode, the byte limit re-encodes from the same pixels and then
 * refuses before anything is written, nothing is left for a buffer without pixels or a folder that
 * does not exist, a file the save never wrote is never removed, and a partly written file that
 * cannot be removed is reported.
 */

#include "testing/testing.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#ifndef _WIN32
#  include <sys/stat.h>
#  include <unistd.h>
#endif

#include "BKE_gtest_base.hh"

#include "BLI_fileops.h"
#include "BLI_path_utils.hh"
#include "BLI_tempfile.h"

#include "IMB_imbuf.hh"
#include "IMB_imbuf_types.hh"

#include "bpy_angelo_clipboard.hh"

namespace blender::angelo_clipboard::tests {

class ClipboardPngTest : public bke::BlenderGTestBase {
 protected:
  std::string directory_;

  void SetUp() override
  {
    char temporary[FILE_MAX];
    BLI_temp_directory_path_get(temporary, sizeof(temporary));
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    char candidate[FILE_MAX];
    BLI_path_join(candidate,
                  sizeof(candidate),
                  temporary,
                  ("angelo_clipboard_png_test_" + std::to_string(nonce)).c_str());
    ASSERT_TRUE(BLI_dir_create_recursive(candidate));
    directory_ = candidate;
  }

  void TearDown() override
  {
    if (!directory_.empty()) {
      BLI_delete(directory_.c_str(), true, true);
    }
  }

  std::string path(const char *name) const
  {
    char result[FILE_MAX];
    BLI_path_join(result, sizeof(result), directory_.c_str(), name);
    return result;
  }

  /** A picture as the clipboard gives it (`WM_clipboard_image_get`): RGBA bytes, \a alpha_at
   * pixel 0 and opaque elsewhere, with a gradient so every pixel differs. */
  static ImBuf *clipboard_picture(const int width, const int height, const uint8_t alpha_at)
  {
    ImBuf *ibuf = IMB_allocImBuf(width, height, ImBufFlags::ByteData);
    uint8_t *pixels = ibuf->byte_data_for_write();
    for (int i = 0; i < width * height; i++) {
      pixels[i * 4 + 0] = uint8_t(i * 7);
      pixels[i * 4 + 1] = uint8_t(i * 13 + 5);
      pixels[i * 4 + 2] = uint8_t(255 - i);
      pixels[i * 4 + 3] = i == 0 ? alpha_at : 255;
    }
    return ibuf;
  }

  static ImBuf *load(const std::string &filepath)
  {
    return IMB_load_image_from_filepath(filepath.c_str(), ImBufFlags::ByteData);
  }
};

TEST_F(ClipboardPngTest, OpaquePictureIsWrittenWithoutAlphaAndSamePixels)
{
  ImBuf *picture = clipboard_picture(37, 23, 255);
  const std::string filepath = path("opaque.png");
  ASSERT_TRUE(angelo_clipboard_write_png(picture, filepath.c_str(), 15));
  ImBuf *read = load(filepath);
  ASSERT_NE(read, nullptr);
  EXPECT_EQ(read->x, 37);
  EXPECT_EQ(read->y, 23);
  EXPECT_EQ(read->ftype, IMB_FTYPE_PNG);
  EXPECT_EQ(read->color_mode, ImColorMode::RGB);
  ASSERT_NE(read->byte_data(), nullptr);
  for (int i = 0; i < 37 * 23 * 4; i++) {
    ASSERT_EQ(read->byte_data()[i], picture->byte_data()[i]) << "byte " << i;
  }
  IMB_freeImBuf(read);
  IMB_freeImBuf(picture);
}

TEST_F(ClipboardPngTest, TranslucentPictureKeepsItsAlpha)
{
  ImBuf *picture = clipboard_picture(16, 9, 128);
  const std::string filepath = path("translucent.png");
  ASSERT_TRUE(angelo_clipboard_write_png(picture, filepath.c_str(), 15));
  ImBuf *read = load(filepath);
  ASSERT_NE(read, nullptr);
  EXPECT_EQ(read->color_mode, ImColorMode::RGBA);
  EXPECT_EQ(read->byte_data()[3], 128);
  for (int i = 1; i < 16 * 9; i++) {
    ASSERT_EQ(read->byte_data()[i * 4 + 3], 255) << "pixel " << i;
    for (int c = 0; c < 3; c++) {
      ASSERT_EQ(read->byte_data()[i * 4 + c], picture->byte_data()[i * 4 + c]) << "pixel " << i;
    }
  }
  /* Straight alpha in, straight alpha out: the translucent pixel's colour is not darkened. */
  for (int c = 0; c < 3; c++) {
    EXPECT_LE(std::abs(int(read->byte_data()[c]) - int(picture->byte_data()[c])), 1);
  }
  IMB_freeImBuf(read);
  IMB_freeImBuf(picture);
}

TEST_F(ClipboardPngTest, MoreCompressionIsNeverLargerAndReadsTheSame)
{
  ImBuf *picture = clipboard_picture(256, 128, 255);
  const std::string fast = path("fast.png"), small = path("small.png");
  ASSERT_TRUE(angelo_clipboard_write_png(picture, fast.c_str(), 0));
  ASSERT_TRUE(angelo_clipboard_write_png(picture, small.c_str(), 100));
  EXPECT_LE(BLI_file_size(small.c_str()), BLI_file_size(fast.c_str()));
  ImBuf *a = load(fast), *b = load(small);
  ASSERT_NE(a, nullptr);
  ASSERT_NE(b, nullptr);
  for (int i = 0; i < 256 * 128 * 4; i++) {
    ASSERT_EQ(a->byte_data()[i], b->byte_data()[i]) << "byte " << i;
  }
  IMB_freeImBuf(a);
  IMB_freeImBuf(b);
  IMB_freeImBuf(picture);
}

TEST_F(ClipboardPngTest, FloatOnlyPictureIsConvertedAndWritten)
{
  /* A 16-bit PNG, EXR or HDR file loads with float pixels only (GHOST's file and PNG paths). */
  ImBuf *picture = IMB_allocImBuf(8, 4, ImBufFlags::FloatData);
  ASSERT_NE(picture->float_data(), nullptr);
  ASSERT_EQ(picture->byte_data(), nullptr);
  float *pixels = picture->float_data_for_write();
  for (int i = 0; i < 8 * 4; i++) {
    const float value = (i % 3 == 0) ? 0.0f : (i % 3 == 1) ? 1.0f : 0.5f;
    pixels[i * 4 + 0] = pixels[i * 4 + 1] = pixels[i * 4 + 2] = value;
    pixels[i * 4 + 3] = 1.0f;
  }
  const std::string filepath = path("float.png");
  ASSERT_TRUE(angelo_clipboard_write_png(picture, filepath.c_str(), 15));
  ImBuf *read = load(filepath);
  ASSERT_NE(read, nullptr);
  EXPECT_EQ(read->x, 8);
  EXPECT_EQ(read->y, 4);
  EXPECT_EQ(read->color_mode, ImColorMode::RGB);
  EXPECT_EQ(read->byte_data()[0 * 4], 0);
  EXPECT_EQ(read->byte_data()[1 * 4], 255);
  /* Scene linear 0.5 is about 188 in sRGB: the conversion is colour managed, not a cast. */
  EXPECT_GT(read->byte_data()[2 * 4], 160);
  EXPECT_LT(read->byte_data()[2 * 4], 215);
  IMB_freeImBuf(read);
  IMB_freeImBuf(picture);
}

TEST_F(ClipboardPngTest, SaveRefusesALongSideBeforeEncoding)
{
  ImBuf *picture = clipboard_picture(20, 10, 255);
  const std::string filepath = path("wide.png");
  EXPECT_EQ(angelo_clipboard_save(picture, filepath.c_str(), 90, 19, 0), ClipboardSave::TooWide);
  EXPECT_FALSE(BLI_exists(filepath.c_str()));
  /* An encode would have set the compression asked for (90). */
  EXPECT_EQ(picture->foptions.compress, ImbFormatOptions{}.compress) << "never encoded";
  EXPECT_EQ(angelo_clipboard_save(picture, filepath.c_str(), 15, 20, 0), ClipboardSave::Saved);
  EXPECT_TRUE(BLI_exists(filepath.c_str()));
  IMB_freeImBuf(picture);
}

TEST_F(ClipboardPngTest, SaveRecompressesTheSamePixelsThenRefusesWithoutWriting)
{
  ImBuf *picture = clipboard_picture(256, 128, 255);
  const std::string fast = path("fast.png"), small = path("small.png"), target = path("t.png");
  ASSERT_TRUE(angelo_clipboard_write_png(picture, fast.c_str(), 0));
  ASSERT_TRUE(angelo_clipboard_write_png(picture, small.c_str(), 100));
  const int64_t fast_bytes = BLI_file_size(fast.c_str());
  const int64_t small_bytes = BLI_file_size(small.c_str());
  ASSERT_LT(small_bytes, fast_bytes);

  /* No byte limit: one encode at the given compression. */
  EXPECT_EQ(angelo_clipboard_save(picture, target.c_str(), 0, 0, 0), ClipboardSave::Saved);
  EXPECT_EQ(int64_t(BLI_file_size(target.c_str())), fast_bytes);
  /* Over the limit at the given compression, within it at the strongest: saved smaller. */
  EXPECT_EQ(angelo_clipboard_save(picture, target.c_str(), 0, 0, small_bytes),
            ClipboardSave::Saved);
  EXPECT_EQ(int64_t(BLI_file_size(target.c_str())), small_bytes);
  ImBuf *read = load(target);
  ASSERT_NE(read, nullptr);
  for (int i = 0; i < 256 * 128 * 4; i++) {
    ASSERT_EQ(read->byte_data()[i], picture->byte_data()[i]) << "byte " << i;
  }
  IMB_freeImBuf(read);
  /* Over it even at the strongest: refused before anything is written. */
  const std::string refused = path("refused.png");
  EXPECT_EQ(angelo_clipboard_save(picture, refused.c_str(), 0, 0, small_bytes - 1),
            ClipboardSave::TooHeavy);
  EXPECT_FALSE(BLI_exists(refused.c_str()));
  /* Nor is a file already at the path touched by the refusal. */
  EXPECT_EQ(angelo_clipboard_save(picture, target.c_str(), 0, 0, small_bytes - 1),
            ClipboardSave::TooHeavy);
  EXPECT_EQ(int64_t(BLI_file_size(target.c_str())), small_bytes);
  IMB_freeImBuf(picture);
}

TEST_F(ClipboardPngTest, SaveFailsCleanlyWithoutPixelsOrAFolder)
{
  EXPECT_EQ(angelo_clipboard_save(nullptr, path("none.png").c_str(), 15, 0, 0),
            ClipboardSave::Failed);
  ImBuf *empty = IMB_allocImBuf(8, 8, ImBufFlags::Zero);
  EXPECT_EQ(angelo_clipboard_save(empty, path("empty.png").c_str(), 15, 0, 0),
            ClipboardSave::Failed);
  EXPECT_FALSE(BLI_exists(path("empty.png").c_str()));
  IMB_freeImBuf(empty);
  ImBuf *picture = clipboard_picture(4, 4, 255);
  char missing[FILE_MAX];
  BLI_path_join(missing, sizeof(missing), directory_.c_str(), "no-such-folder", "picture.png");
  EXPECT_EQ(angelo_clipboard_save(picture, missing, 15, 0, 1024), ClipboardSave::Failed);
  EXPECT_FALSE(BLI_exists(missing));
  IMB_freeImBuf(picture);
}

TEST_F(ClipboardPngTest, AFileTheSaveNeverWroteIsKept)
{
  /* A file already at the path: a save that fails or is refused before writing it never removes
   * it, whatever it holds. */
  const std::string existing = path("existing.png");
  const char *contents = "not written by the clipboard";
  {
    FILE *file = BLI_fopen(existing.c_str(), "wb");
    ASSERT_NE(file, nullptr);
    fputs(contents, file);
    fclose(file);
  }
  const int64_t bytes = int64_t(strlen(contents));
  ImBuf *empty = IMB_allocImBuf(8, 8, ImBufFlags::Zero); /* No pixels: fails before writing. */
  ImBuf *picture = clipboard_picture(64, 64, 255);
  EXPECT_EQ(angelo_clipboard_save(nullptr, existing.c_str(), 15, 0, 0), ClipboardSave::Failed);
  EXPECT_EQ(angelo_clipboard_save(empty, existing.c_str(), 15, 0, 0), ClipboardSave::Failed);
  EXPECT_EQ(angelo_clipboard_save(picture, existing.c_str(), 15, 63, 0), ClipboardSave::TooWide);
  EXPECT_EQ(angelo_clipboard_save(picture, existing.c_str(), 15, 0, 1), ClipboardSave::TooHeavy);
  EXPECT_FALSE(angelo_clipboard_write_png(empty, existing.c_str(), 15));
  ASSERT_TRUE(BLI_exists(existing.c_str())) << "a file this call never wrote is never removed";
  EXPECT_EQ(int64_t(BLI_file_size(existing.c_str())), bytes) << "and never changed";
  IMB_freeImBuf(empty);
  IMB_freeImBuf(picture);
}

TEST_F(ClipboardPngTest, AFailedWriteRemovesOnlyWhatItStartedAndSaysWhenItCannot)
{
  const std::string written = path("written.png");
  auto make = [](const std::string &filepath) {
    FILE *file = BLI_fopen(filepath.c_str(), "wb");
    ASSERT_NE(file, nullptr);
    fputs("partial", file);
    fclose(file);
  };
  make(written);
  /* Never opened by this call: kept. Any result but a failure: kept. */
  EXPECT_EQ(angelo_clipboard_settle(ClipboardSave::Failed, false, written.c_str()),
            ClipboardSave::Failed);
  EXPECT_EQ(angelo_clipboard_settle(ClipboardSave::Saved, true, written.c_str()),
            ClipboardSave::Saved);
  EXPECT_EQ(angelo_clipboard_settle(ClipboardSave::TooHeavy, true, written.c_str()),
            ClipboardSave::TooHeavy);
  EXPECT_TRUE(BLI_exists(written.c_str()));
  /* Opened by this call and then failed: its partial output is removed. */
  EXPECT_EQ(angelo_clipboard_settle(ClipboardSave::Failed, true, written.c_str()),
            ClipboardSave::Failed);
  EXPECT_FALSE(BLI_exists(written.c_str()));
  EXPECT_EQ(angelo_clipboard_settle(ClipboardSave::Failed, true, written.c_str()),
            ClipboardSave::Failed)
      << "nothing to remove";

#ifndef _WIN32
  /* A partial file that cannot be removed (its folder is read-only) is reported, not ignored. */
  if (geteuid() != 0) { /* The superuser removes it anyway. */
    char folder[FILE_MAX];
    BLI_path_join(folder, sizeof(folder), directory_.c_str(), "read-only");
    ASSERT_TRUE(BLI_dir_create_recursive(folder));
    char stuck[FILE_MAX];
    BLI_path_join(stuck, sizeof(stuck), folder, "partial.png");
    make(stuck);
    ASSERT_EQ(chmod(folder, 0500), 0);
    /* Writable again on every way out of this block, a failed assertion included, so TearDown
     * can remove the folder. */
    struct Writable {
      const char *folder;
      ~Writable()
      {
        chmod(folder, 0700);
      }
    } writable{folder};
    EXPECT_EQ(angelo_clipboard_settle(ClipboardSave::Failed, true, stuck),
              ClipboardSave::FailedFileLeft);
    EXPECT_TRUE(BLI_exists(stuck));
  }
#endif
}

TEST_F(ClipboardPngTest, TheSideRuleIsTheSameBeforeAndAfterDecoding)
{
  /* The header probe and the decoded picture are refused by one rule. */
  EXPECT_TRUE(angelo_clipboard_side_refused(20, 10, 19));
  EXPECT_TRUE(angelo_clipboard_side_refused(10, 20, 19));
  EXPECT_FALSE(angelo_clipboard_side_refused(20, 20, 20));
  EXPECT_FALSE(angelo_clipboard_side_refused(100000, 100000, 0)) << "0 is no limit";
}

TEST_F(ClipboardPngTest, NothingIsWrittenWithoutPixelsOrAFolder)
{
  EXPECT_FALSE(angelo_clipboard_write_png(nullptr, path("none.png").c_str(), 15));
  ImBuf *empty = IMB_allocImBuf(8, 8, ImBufFlags::Zero); /* Neither byte nor float pixels. */
  EXPECT_FALSE(angelo_clipboard_write_png(empty, path("empty.png").c_str(), 15));
  EXPECT_FALSE(BLI_exists(path("empty.png").c_str()));
  IMB_freeImBuf(empty);

  ImBuf *picture = clipboard_picture(4, 4, 255);
  char missing[FILE_MAX];
  BLI_path_join(missing, sizeof(missing), directory_.c_str(), "no-such-folder", "picture.png");
  EXPECT_FALSE(angelo_clipboard_write_png(picture, missing, 15));
  EXPECT_FALSE(BLI_exists(missing));
  IMB_freeImBuf(picture);
}

}  // namespace blender::angelo_clipboard::tests
