/* SPDX-FileCopyrightText: 2022 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BLI_listbase.hh"
#include "BLI_path_utils.hh"
#include "BLI_string.hh"
#include "BLI_string_ref.hh"
#include "BLI_vector.hh"

#include "BKE_appdir.hh"
#include "BKE_global.hh"
#include "BKE_gtest_base.hh"
#include "BKE_idtype.hh"
#include "BKE_image.hh"
#include "BKE_main.hh"

#include "MEM_guardedalloc.h"

#include "testing/testing.h"
#include "gmock/gmock.h"

#include <atomic>
#include <chrono>
#include <thread>

#include "BLI_threads.hh"

#include "IMB_cache.hh"
#include "IMB_imbuf.hh"

#include "DNA_image_types.h"
#include "DNA_userdef_types.h"

#include "RE_pipeline.h"

namespace blender::bke::tests {

using testing::Eq;
using testing::Pointwise;

class UdimTest : public BlenderGTestBase {};

TEST_F(UdimTest, image_ensure_tile_token)
{
  auto verify = [](const char *original, const char *expected) {
    char result[FILE_MAX];

    STRNCPY(result, original);
    BKE_image_ensure_tile_token_filename_only(result, sizeof(result));
    EXPECT_STREQ(result, expected);
  };

  /* Already present tokens. */
  verify("test.<UDIM>.png", "test.<UDIM>.png");
  verify("test.<UVTILE>.png", "test.<UVTILE>.png");

  /* UDIM pattern detection. */
  verify("test.1002.png", "test.<UDIM>.png");
  verify("test-1002-ao.png", "test-<UDIM>-ao.png");
  verify("test_1002_ao.png", "test_<UDIM>_ao.png");
  verify("test.1002.ver0023.png", "test.<UDIM>.ver0023.png");
  verify("test.ver0023.1002.png", "test.ver0023.<UDIM>.png");
  verify("test.1002.1.png", "test.<UDIM>.1.png");
  verify("test.1.1002.png", "test.1.<UDIM>.png");
  verify("test-2022-01-01.1002.png", "test-2022-01-01.<UDIM>.png");
  verify("1111_11.1002.png", "1111_11.<UDIM>.png");
  verify("2111_01.1002.png", "2111_01.<UDIM>.png");
  verify("2022_1002_100200.1002.png", "2022_1002_100200.<UDIM>.png");

  /* UVTILE pattern detection. */
  verify("uv-test.u2_v10.png", "uv-test.<UVTILE>.png");
  verify("uv-test-u2_v10-ao.png", "uv-test-<UVTILE>-ao.png");
  verify("uv-test_u2_v10_ao.png", "uv-test_<UVTILE>_ao.png");
  verify("uv-test.u10_v100.png", "uv-test.<UVTILE>.png");
  verify("u_v-test.u2_v10.png", "u_v-test.<UVTILE>.png");
  verify("u2_v10uv-test.png", "<UVTILE>uv-test.png");
  verify("u2_v10u_v-test.png", "<UVTILE>u_v-test.png");

  /* Patterns which should not be detected as UDIMs. */
  for (const char *incorrect : {"1002.png",
                                "1002test.png",
                                "test1002.png",
                                "test(1002).png",
                                "(1002)test.png",
                                "test-1080p.png",
                                "test-1920x1080.png",
                                "test.123.png",
                                "test.12345.png",
                                "test.uv.png",
                                "test.u1v.png",
                                "test.uv1.png",
                                "test.u_v.png",
                                "test.u1_v.png",
                                "test.u_v2.png",
                                "test.u2v3.png",
                                "test.u123_v1.png",
                                "test.u1_v12345.png"})
  {
    /* These should not result in modifications happening. */
    verify(incorrect, incorrect);
  }
}

TEST_F(UdimTest, image_get_tile_strformat)
{
  eUDIM_TILE_FORMAT tile_format;
  char *udim_pattern;

  /* Parameter validation. */
  udim_pattern = BKE_image_get_tile_strformat(nullptr, &tile_format);
  EXPECT_EQ(udim_pattern, nullptr);

  udim_pattern = BKE_image_get_tile_strformat("", nullptr);
  EXPECT_EQ(udim_pattern, nullptr);

  /* Typical usage. */
  udim_pattern = BKE_image_get_tile_strformat("", &tile_format);
  EXPECT_EQ(tile_format, UDIM_TILE_FORMAT_NONE);
  EXPECT_EQ(udim_pattern, nullptr);

  udim_pattern = BKE_image_get_tile_strformat("test.<UNKNOWN>.png", &tile_format);
  EXPECT_EQ(tile_format, UDIM_TILE_FORMAT_NONE);
  EXPECT_EQ(udim_pattern, nullptr);

  udim_pattern = BKE_image_get_tile_strformat("test.<UDIM>.png", &tile_format);
  EXPECT_EQ(tile_format, UDIM_TILE_FORMAT_UDIM);
  EXPECT_STREQ(udim_pattern, "test.%d.png");
  MEM_delete(udim_pattern);

  udim_pattern = BKE_image_get_tile_strformat("test.<UVTILE>.png", &tile_format);
  EXPECT_EQ(tile_format, UDIM_TILE_FORMAT_UVTILE);
  EXPECT_STREQ(udim_pattern, "test.u%d_v%d.png");
  MEM_delete(udim_pattern);
}

TEST_F(UdimTest, image_get_tile_number_from_filepath)
{
  eUDIM_TILE_FORMAT tile_format;
  char *udim_pattern;
  int tile_number;

  udim_pattern = BKE_image_get_tile_strformat("test.<UDIM>.png", &tile_format);
  EXPECT_EQ(tile_format, UDIM_TILE_FORMAT_UDIM);
  EXPECT_NE(udim_pattern, nullptr);

  /* Parameter validation. */
  EXPECT_FALSE(
      BKE_image_get_tile_number_from_filepath(nullptr, udim_pattern, tile_format, &tile_number));
  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "test.1004.png", nullptr, tile_format, &tile_number));
  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "test.1004.png", udim_pattern, UDIM_TILE_FORMAT_NONE, &tile_number));
  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "test.1004.png", udim_pattern, tile_format, nullptr));

  /* UDIM tile format tests. */
  EXPECT_TRUE(BKE_image_get_tile_number_from_filepath(
      "test.1004.png", udim_pattern, tile_format, &tile_number));
  EXPECT_EQ(tile_number, 1004);

  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "has_no_number.png", udim_pattern, tile_format, &tile_number));
  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "test.X.png", udim_pattern, tile_format, &tile_number));
  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "wrong.1004.png", udim_pattern, tile_format, &tile_number));

  MEM_delete(udim_pattern);

  /* UVTILE tile format tests. */
  udim_pattern = BKE_image_get_tile_strformat("test.<UVTILE>.png", &tile_format);
  EXPECT_EQ(tile_format, UDIM_TILE_FORMAT_UVTILE);
  EXPECT_NE(udim_pattern, nullptr);

  EXPECT_TRUE(BKE_image_get_tile_number_from_filepath(
      "test.u2_v2.png", udim_pattern, tile_format, &tile_number));
  EXPECT_EQ(tile_number, 1012);

  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "has_no_number.png", udim_pattern, tile_format, &tile_number));
  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "test.u1_vX.png", udim_pattern, tile_format, &tile_number));
  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "test.uX_v1.png", udim_pattern, tile_format, &tile_number));
  EXPECT_FALSE(BKE_image_get_tile_number_from_filepath(
      "wrong.u2_v2.png", udim_pattern, tile_format, &tile_number));

  MEM_delete(udim_pattern);
}

TEST_F(UdimTest, image_set_filepath_from_tile_number)
{
  eUDIM_TILE_FORMAT tile_format;
  char *udim_pattern;

  udim_pattern = BKE_image_get_tile_strformat("test.<UDIM>.png", &tile_format);
  EXPECT_EQ(tile_format, UDIM_TILE_FORMAT_UDIM);
  EXPECT_NE(udim_pattern, nullptr);

  char filepath[FILE_MAX];

  /* Parameter validation. */
  STRNCPY(filepath, "xxxx");

  BKE_image_set_filepath_from_tile_number(nullptr, udim_pattern, tile_format, 1028);
  BKE_image_set_filepath_from_tile_number(filepath, nullptr, tile_format, 1028);
  EXPECT_STREQ(filepath, "xxxx");
  BKE_image_set_filepath_from_tile_number(filepath, udim_pattern, UDIM_TILE_FORMAT_NONE, 1028);
  EXPECT_STREQ(filepath, "xxxx");

  /* UDIM tile format tests. */
  BKE_image_set_filepath_from_tile_number(filepath, udim_pattern, tile_format, 1028);
  EXPECT_STREQ(filepath, "test.1028.png");
  MEM_delete(udim_pattern);

  /* UVTILE tile format tests. */
  udim_pattern = BKE_image_get_tile_strformat("test.<UVTILE>.png", &tile_format);
  EXPECT_EQ(tile_format, UDIM_TILE_FORMAT_UVTILE);
  EXPECT_NE(udim_pattern, nullptr);

  BKE_image_set_filepath_from_tile_number(filepath, udim_pattern, tile_format, 1028);
  EXPECT_STREQ(filepath, "test.u8_v3.png");
  MEM_delete(udim_pattern);
}

class ImageTest : public BlenderGTestBase {
  Main *bmain_ = nullptr;

  RenderResult *get_image_render_result(Image &image)
  {
    ImageUser iuser{};
    BKE_imageuser_default(&iuser);

    ImBuf *temp_ibuf = BKE_image_acquire_ibuf(&image, &iuser, nullptr);
    BKE_image_release_ibuf(&image, temp_ibuf, nullptr);

    return image.rr;
  }

 protected:
  void SetUp() override
  {
    bmain_ = BKE_main_new();
    G_MAIN = bmain_;
  }

  void TearDown() override
  {
    BKE_main_free(bmain_);
    G_MAIN = nullptr;
  }

  Image *load_image(const char *path)
  {
    const std::string asset_dir = blender::tests::flags_test_asset_dir();
    return BKE_image_load(bmain_, (asset_dir + SEP_STR + "imbuf_io" + SEP_STR + path).c_str());
  }

  Vector<std::string> get_image_layer_names(Image &image)
  {
    RenderResult *render_result = get_image_render_result(image);
    if (!render_result) {
      ADD_FAILURE() << "Missing image RenderResult";
      return {};
    }

    Vector<std::string> layer_names;
    for (const RenderLayer &layer : render_result->layers) {
      layer_names.append(layer.name);
    }

    return layer_names;
  }

  Vector<std::string> get_image_pass_names_for_layer(Image &image, StringRefNull layer_name)
  {
    RenderResult *render_result = get_image_render_result(image);
    if (!render_result) {
      ADD_FAILURE() << "Missing image RenderResult";
      return {};
    }

    for (const RenderLayer &layer : render_result->layers) {
      if (layer.name == layer_name) {
        Vector<std::string> pass_names;
        for (const RenderPass &pass : layer.passes) {
          pass_names.append(pass.name);
        }
        return pass_names;
      }
    }

    return {};
  }
};

/* BKE_image_is_float releases exactly what it acquired, buffer or none. On a failed load
 * (a missing file) it once released the address of the lock (7eb8cd0aba98), which unlocks the
 * viewer lock it never took and releases a bogus render result. The test thread holds the viewer
 * lock across the call; another thread must still be unable to take it afterwards. */
TEST_F(ImageTest, is_float_releases_only_what_it_acquired)
{
  const float color[4] = {0.0f, 0.0f, 0.0f, 1.0f};
  Main *bmain = BKE_main_new();
  EXPECT_FALSE(BKE_image_is_float(nullptr));
  Image *floats = BKE_image_add_generated(
      bmain, 8, 8, "floats", 32, true, IMA_GENTYPE_BLANK, color, false, false, false);
  Image *bytes = BKE_image_add_generated(
      bmain, 8, 8, "bytes", 32, false, IMA_GENTYPE_BLANK, color, false, false, false);
  EXPECT_TRUE(BKE_image_is_float(floats));
  EXPECT_FALSE(BKE_image_is_float(bytes));

  Image *missing = BKE_image_add_generated(
      bmain, 8, 8, "missing", 32, false, IMA_GENTYPE_BLANK, color, false, false, false);
  missing->source = IMA_SRC_FILE;
  STRNCPY(missing->filepath, "//angelo-image-test-missing/does-not-exist.png");
  BKE_image_free_buffers(missing);

  BLI_thread_lock(LOCK_VIEWER);
  EXPECT_FALSE(BKE_image_is_float(missing));
  std::atomic<bool> taken = false;
  std::thread other([&taken]() {
    BLI_thread_lock(LOCK_VIEWER);
    taken = true;
    BLI_thread_unlock(LOCK_VIEWER);
  });
  std::this_thread::sleep_for(std::chrono::milliseconds(200));
  const bool taken_while_held = taken;
  BLI_thread_unlock(LOCK_VIEWER);
  other.join();
  EXPECT_FALSE(taken_while_held) << "the failed load released the viewer lock it never took";
  EXPECT_TRUE(taken);
  BKE_main_free(bmain);
}

TEST_F(ImageTest, garbage_collection_keeps_unsaved_edits)
{
  /* #164448: painted pixels are only in the buffer, so a timed-out edited image keeps it; an
   * unedited one can be reloaded and is collected. */
  const float color[4] = {0.0f, 0.0f, 0.0f, 1.0f};
  Main *bmain = BKE_main_new();
  Image *edited = BKE_image_add_generated(
      bmain, 8, 8, "edited", 32, false, IMA_GENTYPE_BLANK, color, false, false, false);
  Image *clean = BKE_image_add_generated(
      bmain, 8, 8, "clean", 32, false, IMA_GENTYPE_BLANK, color, false, false, false);
  for (Image *image : {edited, clean}) {
    ImBuf *ibuf = BKE_image_acquire_ibuf(image, nullptr, nullptr);
    ASSERT_NE(ibuf, nullptr);
    if (image == edited) {
      ibuf->userflags |= IB_BITMAPDIRTY;
    }
    ibuf->lastused = 0;
    ibuf->gpu.lastused = 0;
    BKE_image_release_ibuf(image, ibuf, nullptr);
  }

  const int textimeout = U.textimeout;
  const int texcollectrate = U.texcollectrate;
  U.textimeout = 1;
  U.texcollectrate = 1;
  BKE_image_free_old_buffers(bmain);
  U.textimeout = textimeout;
  U.texcollectrate = texcollectrate;

  EXPECT_TRUE(BKE_image_has_loaded_ibuf(edited));
  EXPECT_TRUE(BKE_image_is_dirty(edited));
  EXPECT_FALSE(BKE_image_has_loaded_ibuf(clean));
  BKE_main_free(bmain);
}

TEST_F(ImageTest, multilayer)
{
  /* Multi-layer file from another DCC originally reported as #108980.
   * The expected passes are obtained from Blender 4.2 Beta f069692caf8, with the
   * !118867 reverted. File Scene_RenderLayer_000.exr from the report was used. */
  {
    Image *image = load_image("multilayer" SEP_STR "108980.exr");
    ASSERT_NE(image, nullptr);

    EXPECT_THAT(get_image_layer_names(*image), Pointwise(Eq(), {""}));
    EXPECT_THAT(get_image_pass_names_for_layer(*image, ""),
                Pointwise(Eq(),
                          {"Combined",
                           "Albedo",
                           "Nsx",
                           "Nsy",
                           "Nsz",
                           "Nx",
                           "Ny",
                           "Nz",
                           "Px",
                           "Py",
                           "Pz",
                           "RelativeVariance",
                           "Variance",
                           "dzdx",
                           "dzdy",
                           "u"}));
  }

  /* Multi-layer file from another DCC originally reported as #124217.
   * The expected passes are obtained from Blender 4.2 Beta f069692caf8, with the
   * !118867 reverted. File test.exr from the report was used. */
  {
    Image *image = load_image("multilayer" SEP_STR "124217.exr");
    ASSERT_NE(image, nullptr);

    EXPECT_THAT(get_image_layer_names(*image), Pointwise(Eq(), {""}));
    EXPECT_THAT(get_image_pass_names_for_layer(*image, ""),
                Pointwise(Eq(),
                          {"Combined",
                           "Depth",
                           "AO",
                           "ID",
                           "crypto_material",
                           "crypto_material00",
                           "crypto_material01",
                           "crypto_material02",
                           "crypto_material03",
                           "crypto_material04",
                           "crypto_material05",
                           "crypto_material06",
                           "crypto_object",
                           "crypto_object00",
                           "crypto_object01",
                           "crypto_object02",
                           "crypto_object03",
                           "crypto_object04",
                           "crypto_object05",
                           "crypto_object06",
                           "diffuse",
                           "opacity",
                           "specular",
                           "v"}));
  }

  /* Multi-part file from another DCC, originally reported as #101227.
   * The expected passes are obtained from Blender 4.2 Beta f069692caf8, with the
   * !118867 landed. */
  {
    Image *image = load_image("multilayer" SEP_STR "101227.exr");
    ASSERT_NE(image, nullptr);

    EXPECT_THAT(get_image_layer_names(*image), Pointwise(Eq(), {""}));
    EXPECT_THAT(get_image_pass_names_for_layer(*image, ""),
                Pointwise(Eq(), {"C", "N", "albedo", "depth"}));
  }
}

}  // namespace blender::bke::tests
