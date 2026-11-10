/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* Angelo: `Image.angelo_source_revision` changes when what the pixels are loaded FROM changes, and
 * never on a re-load of an unchanged source, a free, or a GPU texture rebuild. */

#include <chrono>
#ifdef WIN32
#  include <io.h>
#else
#  include <unistd.h>
#endif
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <set>
#include <string>

#include "testing/testing.h"

#include "MEM_guardedalloc.h"

#include "BLI_string.h"

#include "BKE_angelo_image_source.hh"
#include "BKE_global.hh"
#include "BKE_gtest_base.hh"
#include "BKE_image.hh"
#include "BKE_image_partial_update.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_packedFile.hh"

#include "IMB_imbuf.hh"
#include "IMB_imbuf_types.hh"

#include "DNA_image_types.h"

namespace blender::bke::tests {

namespace fs = std::filesystem;

class AngeloImageSourceTest : public BlenderGTestBase {
 protected:
  Main *bmain = nullptr;
  Main *prev_bmain = nullptr;
  fs::path dir;

  void SetUp() override
  {
    bmain = BKE_main_new();
    prev_bmain = G_MAIN;
    G_MAIN = bmain;
    dir = fs::temp_directory_path() /
          ("angelo_image_source_test_" +
           std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    fs::create_directories(dir);
  }

  void TearDown() override
  {
    G_MAIN = prev_bmain;
    BKE_main_free(bmain);
    std::error_code ec;
    fs::remove_all(dir, ec);
  }

  std::string path(const char *name) const
  {
    return (dir / name).string();
  }

  /** An image file of `width` x `height` pixels all of grey `value` (PNG unless told otherwise). */
  static void write_png(const std::string &filepath,
                        int width,
                        int height,
                        uint8_t value,
                        eImbFileType ftype = IMB_FTYPE_PNG)
  {
    ImBuf *ibuf = IMB_allocImBuf(width, height, ImBufFlags::ByteData);
    std::memset(ibuf->byte_data_for_write(), value, size_t(width) * height * 4);
    ibuf->ftype = ftype;
    ASSERT_TRUE(IMB_save_image(ibuf, filepath.c_str(), ImBufFlags::ByteData));
    IMB_freeImBuf(ibuf);
  }

  /** Acquire and release once: loads when nothing is cached. Returns the first byte read. */
  static int read_pixels(Image *image, int tile = 0)
  {
    ImageUser iuser{};
    BKE_imageuser_default(&iuser);
    iuser.tile = tile;
    ImBuf *ibuf = BKE_image_acquire_ibuf(image, tile ? &iuser : nullptr, nullptr);
    const int value = (ibuf && ibuf->byte_buffer.data) ? ibuf->byte_buffer.data[0] : -1;
    BKE_image_release_ibuf(image, ibuf, nullptr);
    return value;
  }

  static uint64_t source(const Image *image)
  {
    return image->runtime->angelo_source_revision.load();
  }
  static uint64_t pixels(const Image *image)
  {
    return image->runtime->angelo_pixel_revision.load();
  }
};

TEST_F(AngeloImageSourceTest, descriptor_witness_describes_decoder_input_not_path_metadata)
{
  write_png(path("a.png"), 4, 4, 10);
  write_png(path("label.png"), 8, 8, 20);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  FILE *input = std::fopen(path("a.png").c_str(), "rb");
  ASSERT_NE(input, nullptr);
#ifdef WIN32
  const int fd = _fileno(input);
#else
  const int fd = fileno(input);
#endif
  /* A diagnostic filepath is not evidence: the decoder and witness use the same descriptor,
   * even when the supplied label points at a different file. No concurrent writer is needed. */
  angelo_image_source_note_file_load(
      *image, 0, 0, 0, angelo_image_source_prepare_file_load(fd, path("label.png").c_str()), 0);
  EXPECT_TRUE(angelo_image_source_loads_supported(*image));
  const uint64_t start = source(image);
  write_png(path("label.png"), 16, 16, 30);
  angelo_image_source_note_file_load(
      *image, 0, 0, 0, angelo_image_source_prepare_file_load(fd, path("label.png").c_str()), 0);
  EXPECT_EQ(source(image), start);
  ImBuf *decoded = IMB_load_image_from_file_descriptor(
      fd, ImBufFlags::ByteData, path("label.png").c_str());
  ASSERT_NE(decoded, nullptr);
  EXPECT_EQ(decoded->x, 4);
  EXPECT_EQ(decoded->byte_buffer.data[0], 10);
  IMB_freeImBuf(decoded);
  std::fclose(input);

  input = std::fopen(path("label.png").c_str(), "rb");
  ASSERT_NE(input, nullptr);
#ifdef WIN32
  const int other_fd = _fileno(input);
#else
  const int other_fd = fileno(input);
#endif
  angelo_image_source_note_file_load(
      *image, 0, 0, 0, angelo_image_source_prepare_file_load(other_fd, path("label.png").c_str()), 0);
  EXPECT_NE(source(image), start);
  std::fclose(input);
}

TEST_F(AngeloImageSourceTest, unavailable_and_opaque_decoder_witnesses_are_unsupported)
{
  write_png(path("a.png"), 4, 4, 10);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  EXPECT_FALSE(angelo_image_source_loads_supported(*image));
  EXPECT_EQ(read_pixels(image), 10);
  EXPECT_TRUE(angelo_image_source_loads_supported(*image));
  const uint64_t pixel_revision = pixels(image);
  angelo_image_source_note_unsupported_load(*image, 0);
  EXPECT_FALSE(angelo_image_source_loads_supported(*image));
  const uint64_t unsupported = source(image);
  angelo_image_source_note_unsupported_load(*image, 0);
  EXPECT_EQ(source(image), unsupported);
  angelo_image_source_changed(*image);
  EXPECT_FALSE(angelo_image_source_loads_supported(*image));
  /* Match the actual decoder slot, not a separate synthetic unsupported slot. */
  const int base_tile = BKE_image_get_tile(image, 0)->tile_number;
  angelo_image_source_note_file_load(
      *image, 0, base_tile, 0, angelo_image_source_prepare_file_load(-1, path("a.png").c_str()), 0);
  EXPECT_FALSE(angelo_image_source_loads_supported(*image));
  EXPECT_EQ(pixels(image), pixel_revision);
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 10);
  EXPECT_TRUE(angelo_image_source_loads_supported(*image));
}

TEST_F(AngeloImageSourceTest, inferred_color_space_is_stable_across_unchanged_reloads)
{
  write_png(path("automatic.png"), 4, 4, 10);
  for (const bool packed : {false, true}) {
    Image *image = BKE_image_load(bmain, path("automatic.png").c_str());
    ASSERT_NE(image, nullptr);
    if (packed) {
      BKE_image_packfiles(nullptr, image, bmain->filepath);
      ASSERT_NE(image->packedfiles.first, nullptr);
    }
    /* UDIM paths cannot be probed before a real tile read. Unresolved color space is also
     * legitimate for packed bytes: the first decode supplies the effective default. */
    image->colorspace_settings.name[0] = '\0';
    const uint64_t start = source(image);
    EXPECT_EQ(read_pixels(image), 10);
    ASSERT_NE(image->colorspace_settings.name[0], '\0');
    EXPECT_EQ(source(image), start);
    for (int i = 0; i < 2; i++) {
      BKE_image_free_buffers(image);
      EXPECT_EQ(read_pixels(image), 10);
      EXPECT_EQ(source(image), start);
    }
  }
}

TEST_F(AngeloImageSourceTest, reloading_an_unchanged_file_never_changes_it)
{
  write_png(path("a.png"), 4, 4, 10);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  const uint64_t start = source(image);
  EXPECT_EQ(read_pixels(image), 10);
  for (int i = 0; i < 3; i++) {
    EXPECT_EQ(read_pixels(image), 10); /* Cached. */
    BKE_image_free_buffers(image);     /* Memory pressure. */
    EXPECT_EQ(read_pixels(image), 10); /* Re-loaded from the same file. */
  }
  EXPECT_EQ(source(image), start);
}

TEST_F(AngeloImageSourceTest, file_changed_on_disk_then_reloaded_after_eviction_changes_it)
{
  write_png(path("a.png"), 4, 4, 10);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  EXPECT_EQ(read_pixels(image), 10);
  const uint64_t start = source(image);

  /* No RELOAD signal anywhere: the file changes, the buffers are freed, the pixels are read. */
  write_png(path("a.png"), 8, 8, 200);
  EXPECT_EQ(source(image), start); /* Nothing read yet: the pixels in memory are unchanged. */
  BKE_image_free_buffers(image);
  EXPECT_EQ(source(image), start);
  EXPECT_EQ(read_pixels(image), 200);
  const uint64_t after = source(image);
  EXPECT_NE(after, start);

  /* The same new file again: unchanged. */
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 200);
  EXPECT_EQ(source(image), after);
}

TEST_F(AngeloImageSourceTest, same_size_rewrite_with_the_old_modified_time_still_changes_it)
{
  /* BMP: uncompressed, so the rewrite has exactly the same size. */
  write_png(path("a.bmp"), 4, 4, 10, IMB_FTYPE_BMP);
  Image *image = BKE_image_load(bmain, path("a.bmp").c_str());
  EXPECT_EQ(read_pixels(image), 10);
  const uint64_t start = source(image);
  const auto modified = fs::last_write_time(path("a.bmp"));
  const auto bytes = fs::file_size(path("a.bmp"));

  write_png(path("a.bmp"), 4, 4, 11, IMB_FTYPE_BMP);
  fs::last_write_time(path("a.bmp"), modified); /* The change time still moves. */
  ASSERT_EQ(fs::file_size(path("a.bmp")), bytes);
  ASSERT_EQ(fs::last_write_time(path("a.bmp")), modified);
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 11);
  EXPECT_NE(source(image), start);
}

TEST_F(AngeloImageSourceTest, a_file_that_disappears_or_returns_changes_it)
{
  write_png(path("a.png"), 4, 4, 10);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  EXPECT_EQ(read_pixels(image), 10);
  const uint64_t start = source(image);
  fs::remove(path("a.png"));
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), -1);
  const uint64_t missing = source(image);
  EXPECT_NE(missing, start);
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), -1);
  EXPECT_EQ(source(image), missing);
  write_png(path("a.png"), 4, 4, 30);
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 30);
  EXPECT_NE(source(image), missing);
}

TEST_F(AngeloImageSourceTest, reload_source_type_and_path_changes_change_it)
{
  write_png(path("a.png"), 4, 4, 10);
  write_png(path("b.png"), 4, 4, 20);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  EXPECT_EQ(read_pixels(image), 10);

  uint64_t last = source(image);
  BKE_image_signal(bmain, image, nullptr, IMA_SIGNAL_RELOAD);
  EXPECT_NE(source(image), last);
  last = source(image);
  EXPECT_EQ(read_pixels(image), 10);
  EXPECT_EQ(source(image), last); /* The reload already counted; its load reads the same file. */

  image->source = IMA_SRC_SEQUENCE;
  BKE_image_signal(bmain, image, nullptr, IMA_SIGNAL_SRC_CHANGE);
  EXPECT_NE(source(image), last);
  image->source = IMA_SRC_FILE;
  BKE_image_signal(bmain, image, nullptr, IMA_SIGNAL_SRC_CHANGE);
  EXPECT_EQ(read_pixels(image), 10);
  last = source(image);

  /* A path written without any signal (`filepath_raw`) is caught at the next load. */
  STRNCPY(image->filepath, path("b.png").c_str());
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 20);
  EXPECT_NE(source(image), last);
}

TEST_F(AngeloImageSourceTest, other_load_settings_change_it)
{
  write_png(path("a.png"), 4, 4, 10);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  EXPECT_EQ(read_pixels(image), 10);
  const uint64_t start = source(image);
  image->alpha_mode = (image->alpha_mode == IMA_ALPHA_PREMUL) ? IMA_ALPHA_STRAIGHT :
                                                                IMA_ALPHA_PREMUL;
  BKE_image_free_buffers(image);
  read_pixels(image);
  EXPECT_NE(source(image), start);
}

TEST_F(AngeloImageSourceTest, packing_and_other_packed_bytes_change_it_and_reloads_do_not)
{
  write_png(path("a.png"), 4, 4, 10);
  write_png(path("b.png"), 4, 4, 20);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  EXPECT_EQ(read_pixels(image), 10);
  uint64_t last = source(image);

  auto pack_file = [&](const char *name) {
    const std::string filepath = path(name);
    const size_t size = size_t(fs::file_size(filepath));
    char *data = static_cast<char *>(MEM_new_array_uninitialized(size, 1, __func__));
    FILE *file = fopen(filepath.c_str(), "rb");
    ASSERT_NE(file, nullptr);
    ASSERT_EQ(fread(data, 1, size, file), size);
    fclose(file);
    BKE_image_free_packedfiles(image);
    BKE_image_packfiles_from_mem(nullptr, image, data, size);
  };

  pack_file("a.png");
  EXPECT_FALSE(angelo_image_source_loads_supported(*image));
  EXPECT_NE(source(image), last);
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 10);
  last = source(image);
  for (int i = 0; i < 2; i++) {
    BKE_image_free_buffers(image);
    EXPECT_EQ(read_pixels(image), 10);
  }
  EXPECT_EQ(source(image), last);
  EXPECT_TRUE(angelo_image_source_loads_supported(*image));

  pack_file("b.png");
  EXPECT_NE(source(image), last);
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 20);
}

TEST_F(AngeloImageSourceTest, actual_unpack_invalidates_packed_source_without_a_reload)
{
  write_png(path("a.png"), 4, 4, 10);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  ASSERT_NE(image, nullptr);
  BKE_image_packfiles(nullptr, image, "");
  ASSERT_TRUE(BKE_image_has_packedfile(image));
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 10);
  ASSERT_TRUE(angelo_image_source_loads_supported(*image));
  const uint64_t packed = source(image);

  /* Exercise the real RNA/operator owner, which removes records without free_packedfiles().
   * Its extraction return code does not decide whether the packed source actually changed. */
  BKE_packedfile_unpack_image(bmain, nullptr, image, PF_USE_ORIGINAL);
  EXPECT_FALSE(BKE_image_has_packedfile(image));
  EXPECT_NE(source(image), packed);
  EXPECT_FALSE(angelo_image_source_loads_supported(*image));
  const uint64_t unpacked = source(image);
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 10);
  EXPECT_EQ(source(image), unpacked);
  EXPECT_TRUE(angelo_image_source_loads_supported(*image));

  BKE_packedfile_unpack_image(bmain, nullptr, image, PF_USE_ORIGINAL);
  EXPECT_EQ(source(image), unpacked);
  EXPECT_TRUE(angelo_image_source_loads_supported(*image));
}

TEST_F(AngeloImageSourceTest, udim_tiles_load_independently)
{
  write_png(path("t.1001.png"), 4, 4, 10);
  write_png(path("t.1002.png"), 4, 4, 20);
  Image *image = BKE_image_load(bmain, path("t.<UDIM>.png").c_str());
  ASSERT_NE(image, nullptr);
  image->source = IMA_SRC_TILED;
  BKE_image_signal(bmain, image, nullptr, IMA_SIGNAL_SRC_CHANGE);
  BKE_image_add_tile(image, 1002, nullptr);
  EXPECT_EQ(read_pixels(image, 1001), 10);
  EXPECT_EQ(read_pixels(image, 1002), 20);
  const uint64_t start = source(image);
  for (int i = 0; i < 2; i++) {
    BKE_image_free_buffers(image);
    EXPECT_EQ(read_pixels(image, 1002), 20);
    EXPECT_EQ(read_pixels(image, 1001), 10);
  }
  EXPECT_EQ(source(image), start);

  write_png(path("t.1002.png"), 8, 8, 21);
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image, 1001), 10);
  EXPECT_EQ(source(image), start);
  EXPECT_EQ(read_pixels(image, 1002), 21);
  EXPECT_NE(source(image), start);
}

TEST_F(AngeloImageSourceTest, pixel_edits_and_gpu_rebuilds_leave_it_unchanged)
{
  write_png(path("a.png"), 4, 4, 10);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  EXPECT_EQ(read_pixels(image), 10);
  const uint64_t start = source(image);
  const uint64_t pixels_start = pixels(image);

  BKE_image_partial_update_mark_full_update(image);
  EXPECT_NE(pixels(image), pixels_start);
  EXPECT_EQ(source(image), start);

  ImageUser pass_user{};
  BKE_imageuser_default(&pass_user);
  pass_user.pass = 1;
  pass_user.layer = 1;
  pass_user.framenr = 7;
  BKE_image_partial_update_mark_gpu_stale(image);
  BKE_image_ensure_gpu_texture(image, &pass_user);
  EXPECT_EQ(source(image), start);
}

TEST_F(AngeloImageSourceTest, other_images_are_unaffected_and_values_never_repeat)
{
  write_png(path("a.png"), 4, 4, 10);
  write_png(path("b.png"), 4, 4, 20);
  Image *a = BKE_image_load(bmain, path("a.png").c_str());
  Image *b = BKE_image_load(bmain, path("b.png").c_str());
  EXPECT_EQ(read_pixels(a), 10);
  EXPECT_EQ(read_pixels(b), 20);
  const uint64_t b_start = source(b);
  BKE_image_signal(bmain, a, nullptr, IMA_SIGNAL_RELOAD);
  EXPECT_EQ(source(b), b_start);

  std::set<uint64_t> seen = {source(a), source(b)};
  for (int i = 0; i < 4; i++) {
    BKE_id_free(bmain, a);
    a = BKE_image_load(bmain, path("a.png").c_str());
    EXPECT_TRUE(seen.insert(source(a)).second);
    BKE_image_signal(bmain, a, nullptr, IMA_SIGNAL_RELOAD);
    EXPECT_TRUE(seen.insert(source(a)).second);
  }
  EXPECT_EQ(source(b), b_start);
}

TEST_F(AngeloImageSourceTest, an_undo_step_that_moves_the_record_keeps_the_revision)
{
  write_png(path("a.png"), 4, 4, 10);
  Image *image = BKE_image_load(bmain, path("a.png").c_str());
  EXPECT_EQ(read_pixels(image), 10);
  BKE_image_signal(bmain, image, nullptr, IMA_SIGNAL_RELOAD);
  EXPECT_EQ(read_pixels(image), 10);
  const uint64_t kept = source(image);

  /* What an undo re-read does: a fresh runtime, then #image_foreach_cache moves the record. */
  AngeloImageSourceLoads *record = image->runtime->angelo_source_loads;
  ASSERT_NE(record, nullptr);
  image->runtime->angelo_source_loads = nullptr;
  EXPECT_FALSE(angelo_image_source_loads_supported(*image));
  image->runtime->angelo_source_revision = angelo_image_source_revision_fresh();
  image->runtime->angelo_source_loads = record;
  angelo_image_source_restore_after_undo(*image);
  EXPECT_TRUE(angelo_image_source_loads_supported(*image));
  EXPECT_EQ(source(image), kept);

  /* The record still knows the last load: re-reading the same file changes nothing. */
  BKE_image_free_buffers(image);
  EXPECT_EQ(read_pixels(image), 10);
  EXPECT_EQ(source(image), kept);
}

}  // namespace blender::bke::tests
