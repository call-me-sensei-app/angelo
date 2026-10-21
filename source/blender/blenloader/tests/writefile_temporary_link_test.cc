/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Saving writes `<target>@` first and renames it over the target. Anyone who can create files in
 * the target's folder can plant that predictable name as a link to another file the user can
 * write (the file being saved from, preferences, any project). A save must replace such a link,
 * never truncate and rewrite what it points at, in every write mode that goes through
 * #BLO_write_file: plain, compressed, size-gated compression, auto-save, relative-path remapping,
 * save versions and preferences.
 *
 * Found by an independent break-it review of the headless MCP save path (2026-10-20); the cases
 * below are the general class, not that path.
 */

#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>

#include "DNA_image_types.h"
#include "DNA_scene_types.h"
#include "DNA_userdef_types.h"

#include "BLI_fileops.h"
#include "BLI_listbase.h"
#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "BLI_tempfile.h"

#include "BKE_blendfile.hh"
#include "BKE_global.hh"
#include "BKE_idprop.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"

#include "BLO_readfile.hh"
#include "BLO_writefile.hh"

#include "blendfile_loading_base_test.h"

namespace blender {
namespace {

constexpr int marker_value = 4242;
constexpr const char *victim_bytes = "bytes of a file the save must never touch";

/** How the planted `<target>@` entry reaches the file it would corrupt. */
enum class Plant { SymbolicLink, HardLink };

class WritefileTemporaryLinkTest : public BlendfileLoadingBaseTest {
 protected:
  Main *main_ = nullptr;
  ReportList reports_{};
  std::string directory_;
  int saved_versions_ = 0;
  int saved_fileflags_ = 0;
  std::string saved_last_blend_;

  void SetUp() override
  {
    saved_versions_ = U.versions;
    saved_fileflags_ = G.fileflags;
    saved_last_blend_ = G.filepath_last_blend;
    BKE_reports_init(&reports_, RPT_STORE | RPT_PRINT_HANDLED_BY_OWNER);

    char temporary[FILE_MAX];
    BLI_temp_directory_path_get(temporary, sizeof(temporary));
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    char candidate[FILE_MAX];
    BLI_path_join(candidate,
                  sizeof(candidate),
                  temporary,
                  ("blender_temporary_link_test_" + std::to_string(nonce)).c_str());
    std::error_code error;
    ASSERT_TRUE(std::filesystem::create_directory(path_of(candidate), error)) << error.message();
    directory_ = candidate;
    ASSERT_TRUE(BLI_dir_create_recursive(path("source").c_str()));
    ASSERT_TRUE(BLI_dir_create_recursive(path("saved").c_str()));

    main_ = BKE_main_new();
    ASSERT_NE(main_, nullptr);
    STRNCPY(main_->filepath, path("source/original.blend").c_str());
    Scene *scene = BKE_scene_add(main_, "LinkFixture");
    ASSERT_NE(scene, nullptr);
    ASSERT_TRUE(
        IDP_AddToGroup(IDP_EnsureProperties(&scene->id), IDP_NewInt(marker_value, "marker")));
    Image *image = BKE_id_new<Image>(main_, "Texture");
    ASSERT_NE(image, nullptr);
    image->source = IMA_SRC_FILE;
    STRNCPY(image->filepath, "//textures/wood.png");
  }

  void TearDown() override
  {
    if (main_) {
      BKE_main_free(main_);
    }
    BKE_reports_free(&reports_);
    if (!directory_.empty()) {
      std::error_code error;
      std::filesystem::remove_all(path_of(directory_), error);
      EXPECT_FALSE(bool(error)) << error.message();
    }
    U.versions = saved_versions_;
    G.fileflags = saved_fileflags_;
    STRNCPY(G.filepath_last_blend, saved_last_blend_.c_str());
    BlendfileLoadingBaseTest::TearDown();
  }

  static std::filesystem::path path_of(const std::string &utf8)
  {
    return std::filesystem::path(std::u8string(utf8.begin(), utf8.end()));
  }

  std::string path(const char *name) const
  {
    char result[FILE_MAX];
    BLI_path_join(result, sizeof(result), directory_.c_str(), name);
    return result;
  }

  static std::string read_bytes(const std::string &filepath)
  {
    std::ifstream stream(path_of(filepath), std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
  }

  static void write_bytes(const std::string &filepath, const std::string &bytes)
  {
    std::ofstream stream(path_of(filepath), std::ios::binary | std::ios::trunc);
    stream << bytes;
  }

  static bool is_regular_file_not_link(const std::string &filepath)
  {
    std::error_code error;
    return std::filesystem::symlink_status(path_of(filepath), error).type() ==
           std::filesystem::file_type::regular;
  }

  std::string errors() const
  {
    std::string result;
    for (const Report &report : reports_.list) {
      if (report.type & RPT_ERROR_ALL) {
        result += report.message;
        result += '\n';
      }
    }
    return result;
  }

  /** Plant `link` so that writing through it would write `victim`. */
  static void plant(const Plant kind, const std::string &victim, const std::string &link)
  {
    std::error_code error;
    if (kind == Plant::SymbolicLink) {
      std::filesystem::create_symlink(path_of(victim), path_of(link), error);
    }
    else {
      std::filesystem::create_hard_link(path_of(victim), path_of(link), error);
    }
    ASSERT_FALSE(bool(error)) << error.message();
  }

  /** Read `filepath` back and check it holds this fixture's scene and the image path. */
  void expect_saved_scene(const std::string &filepath, const char *image_path)
  {
    BlendFileReadReport read_reports{};
    read_reports.reports = &reports_;
    BlendFileData *loaded = BLO_read_from_file(
        filepath.c_str(), BLO_READ_SKIP_NONE, &read_reports);
    ASSERT_NE(loaded, nullptr) << errors();
    Scene *scene = reinterpret_cast<Scene *>(
        BKE_libblock_find_name(loaded->main, ID_SCE, "LinkFixture"));
    ASSERT_NE(scene, nullptr);
    IDProperty *marker = IDP_GetPropertyFromGroup(IDP_GetProperties(&scene->id), "marker");
    ASSERT_NE(marker, nullptr);
    EXPECT_EQ(IDP_int_get(marker), marker_value);
    Image *image = reinterpret_cast<Image *>(
        BKE_libblock_find_name(loaded->main, ID_IM, "Texture"));
    ASSERT_NE(image, nullptr);
    EXPECT_STREQ(image->filepath, image_path);
    BLO_blendfiledata_free(loaded);
  }

  /**
   * Plant `<target>@` as a link to a victim file, save with `flags` and `params`, and check the
   * victim is untouched while the target holds the saved scene.
   */
  void save_over_planted_link(const Plant kind,
                              const int flags,
                              const BlendFileWriteParams &params,
                              const char *expected_image_path)
  {
    const std::string victim = path("victim.blend");
    const std::string target = path("saved/target.blend");
    const std::string temporary = target + "@";
    write_bytes(victim, victim_bytes);
    plant(kind, victim, temporary);

    BKE_reports_clear(&reports_);
    ASSERT_TRUE(BLO_write_file(main_, target.c_str(), flags, &params, &reports_)) << errors();

    EXPECT_EQ(read_bytes(victim), victim_bytes);
    EXPECT_FALSE(BLI_exists(temporary.c_str()));
    EXPECT_TRUE(is_regular_file_not_link(target));
    expect_saved_scene(target, expected_image_path);
  }

  static BlendFileWriteParams copy_params()
  {
    BlendFileWriteParams params{};
    params.use_save_as_copy = true;
    return params;
  }
};

/* Symbolic links on Windows need a privilege test machines may lack; hard links do not. */
#ifdef WIN32
#  define SKIP_WITHOUT_SYMLINKS(kind) \
    if ((kind) == Plant::SymbolicLink) { \
      GTEST_SKIP() << "Symbolic links need a privilege on Windows"; \
    }
#else
#  define SKIP_WITHOUT_SYMLINKS(kind)
#endif

class WritefileTemporaryLinkKindTest : public WritefileTemporaryLinkTest,
                                       public testing::WithParamInterface<Plant> {};

TEST_P(WritefileTemporaryLinkKindTest, PlainSaveReplacesLink)
{
  SKIP_WITHOUT_SYMLINKS(GetParam());
  save_over_planted_link(GetParam(), 0, copy_params(), "//textures/wood.png");
}

TEST_P(WritefileTemporaryLinkKindTest, CompressedSaveReplacesLink)
{
  SKIP_WITHOUT_SYMLINKS(GetParam());
  save_over_planted_link(GetParam(), G_FILE_COMPRESS, copy_params(), "//textures/wood.png");
}

TEST_P(WritefileTemporaryLinkKindTest, SizeGatedCompressionReplacesLinkBelowAndAboveTheGate)
{
  SKIP_WITHOUT_SYMLINKS(GetParam());
  BlendFileWriteParams params = copy_params();
  /* Above the file size: stays raw. One byte: switches to compression mid-write, reopening the
   * temporary name, which must still replace rather than fail on its own first file. */
  for (const size_t gate : {size_t(64) << 20, size_t(1)}) {
    params.compression_min_size = gate;
    save_over_planted_link(GetParam(), G_FILE_COMPRESS, params, "//textures/wood.png");
    BLI_delete(path("saved/target.blend").c_str(), false, false);
  }
}

TEST_P(WritefileTemporaryLinkKindTest, AutoSaveFlagsReplaceLink)
{
  SKIP_WITHOUT_SYMLINKS(GetParam());
  /* The flags #WM_autosave_write passes. */
  save_over_planted_link(GetParam(),
                         G_FILE_RECOVER_WRITE | G_FILE_COMPRESS,
                         BlendFileWriteParams{},
                         "//textures/wood.png");
}

TEST_P(WritefileTemporaryLinkKindTest, RelativeRemapReplacesLinkAndRebasesPaths)
{
  SKIP_WITHOUT_SYMLINKS(GetParam());
  BlendFileWriteParams params = copy_params();
  params.remap_mode = BLO_WRITE_PATH_REMAP_RELATIVE;
  save_over_planted_link(GetParam(), 0, params, "//../source/textures/wood.png");
  /* Save-as-copy restores the in-memory path. */
  Image *image = reinterpret_cast<Image *>(BKE_libblock_find_name(main_, ID_IM, "Texture"));
  ASSERT_NE(image, nullptr);
  EXPECT_STREQ(image->filepath, "//textures/wood.png");
}

TEST_P(WritefileTemporaryLinkKindTest, LinkToTheFileBeingSavedFromIsReplaced)
{
  SKIP_WITHOUT_SYMLINKS(GetParam());
  /* The reproduced case's shape: the planted name points at the source document itself. */
  const std::string source = main_->filepath;
  const std::string target = path("saved/copy.blend");
  write_bytes(source, victim_bytes);
  plant(GetParam(), source, target + "@");

  const BlendFileWriteParams params = copy_params();
  ASSERT_TRUE(BLO_write_file(main_, target.c_str(), 0, &params, &reports_)) << errors();
  EXPECT_EQ(read_bytes(source), victim_bytes);
  expect_saved_scene(target, "//textures/wood.png");
}

TEST_P(WritefileTemporaryLinkKindTest, SaveVersionsRotateOverLinksWithoutWritingThroughThem)
{
  SKIP_WITHOUT_SYMLINKS(GetParam());
  /* History rotation renames, which replaces a link at `.blend1` rather than following it. */
  const std::string victim = path("victim.blend");
  const std::string target = path("saved/target.blend");
  write_bytes(victim, victim_bytes);
  write_bytes(target, "previous save");
  plant(GetParam(), victim, target + "1");
  plant(GetParam(), victim, target + "@");

  U.versions = 2;
  BlendFileWriteParams params = copy_params();
  params.use_save_versions = true;
  ASSERT_TRUE(BLO_write_file(main_, target.c_str(), 0, &params, &reports_)) << errors();
  EXPECT_EQ(read_bytes(victim), victim_bytes);
  EXPECT_EQ(read_bytes(target + "1"), "previous save");
  EXPECT_TRUE(is_regular_file_not_link(target + "1"));
  expect_saved_scene(target, "//textures/wood.png");
}

TEST_P(WritefileTemporaryLinkKindTest, PreferencesSaveReplacesLink)
{
  SKIP_WITHOUT_SYMLINKS(GetParam());
  const std::string victim = path("victim.blend");
  const std::string preferences = path("saved/userpref.blend");
  write_bytes(victim, victim_bytes);
  plant(GetParam(), victim, preferences + "@");

  ASSERT_TRUE(BKE_blendfile_userdef_write(preferences.c_str(), &reports_)) << errors();
  EXPECT_EQ(read_bytes(victim), victim_bytes);
  EXPECT_FALSE(BLI_exists((preferences + "@").c_str()));
  EXPECT_EQ(read_bytes(preferences).substr(0, 7), "BLENDER");
}

INSTANTIATE_TEST_SUITE_P(PlantedLinks,
                         WritefileTemporaryLinkKindTest,
                         testing::Values(Plant::SymbolicLink, Plant::HardLink),
                         [](const testing::TestParamInfo<Plant> &info) {
                           return info.param == Plant::SymbolicLink ? "SymbolicLink" : "HardLink";
                         });

TEST_F(WritefileTemporaryLinkTest, DirectoryAtTemporaryNameFailsWithoutTouchingTarget)
{
  const std::string target = path("saved/target.blend");
  write_bytes(target, "previous save");
  ASSERT_TRUE(BLI_dir_create_recursive((target + "@").c_str()));

  const BlendFileWriteParams params = copy_params();
  EXPECT_FALSE(BLO_write_file(main_, target.c_str(), 0, &params, &reports_));
  EXPECT_NE(errors().find("Cannot open file"), std::string::npos) << errors();
  EXPECT_EQ(read_bytes(target), "previous save");
  EXPECT_TRUE(BLI_is_dir((target + "@").c_str()));
}

}  // namespace
}  // namespace blender
