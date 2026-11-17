/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The deep hash of linked data starts with its source file's hash
 * (#compute_linked_id_deep_hashes). An ID read from an older version of the file must never take a
 * newer version's hash (packed data is deduplicated by deep hash: it would stand for the newer
 * content), even when that hash is cached; and a library reached through a symbolic link
 * (`latest.blend -> v1.blend`) is read once per call and not again while it is unchanged, like any
 * other file.
 */

#include "testing/testing.h"

#include "BKE_gtest_base.hh"
#include "BKE_id_hash.hh"
#include "BKE_lib_id.hh"
#include "BKE_library.hh"
#include "BKE_main.hh"

#include "BLI_fileops.hh"
#include "BLI_string.hh"

#include "DNA_ID.h"
#include "DNA_image_types.h"

#include <chrono>
#include <fcntl.h>
#include <filesystem>
#include <fstream>
#include <thread>
#include <variant>

#ifndef WIN32
#  include <unistd.h>
#else
#  include <io.h>
#endif

namespace blender::bke::id_hash::tests {

namespace fs = std::filesystem;

class IDHashSourceFileTest : public BlenderGTestBase {
 protected:
  Main *bmain = nullptr;
  fs::path folder;

  void SetUp() override
  {
    bmain = BKE_main_new();
    const ::testing::TestInfo *info = ::testing::UnitTest::GetInstance()->current_test_info();
    folder = fs::temp_directory_path() /
             (std::string("angelo-id-hash-") + info->name() + "-" +
              std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    fs::create_directories(folder);
  }
  void TearDown() override
  {
    BKE_main_free(bmain);
    std::error_code error;
    fs::remove_all(folder, error);
  }

  /** Write `content` to `path`, its modification time `age` in the past. */
  static void write(const fs::path &path, const std::string &content, std::chrono::seconds age)
  {
    std::ofstream(path, std::ios::binary | std::ios::trunc) << content;
    fs::last_write_time(path, fs::last_write_time(path) - age);
  }

  Library *library(const fs::path &path)
  {
    Library *lib = BKE_id_new<Library>(bmain, "Library");
    STRNCPY(lib->filepath, path.string().c_str());
    STRNCPY(lib->runtime->filepath_abs, path.string().c_str());
    return lib;
  }

  /** A linked ID as the reader leaves it: recording the version of the file it opened. */
  ID *linked_id(Library *lib, const char *name)
  {
    Image *image = BKE_id_new<Image>(bmain, name);
    image->id.lib = lib;
    const int file = BLI_open(lib->runtime->filepath_abs, O_BINARY | O_RDONLY, 0);
    EXPECT_NE(file, -1);
    const std::optional<FileVersion> version = file_version_of_descriptor(file);
    close(file);
    EXPECT_TRUE(version.has_value());
    BLI_stat_t stat;
    EXPECT_NE(BLI_stat(lib->runtime->filepath_abs, &stat), -1);
    image->id.runtime->src_blend_modifification_time = stat.st_mtime;
    source_file_version_record(image->id, *version);
    return &image->id;
  }
};

TEST_F(IDHashSourceFileTest, AnIdFromAnOlderVersionNeverTakesTheNewerVersionsCachedHash)
{
  const fs::path path = folder / "asset.blend";
  write(path, "first version", std::chrono::seconds(20));
  Library *lib = library(path);
  ID *older = linked_id(lib, "Older");

  write(path, "second, longer version", std::chrono::seconds(10));
  /* Its status change time (set with its modification time) two seconds old too: the second
   * version's hash, cached below, is trusted. Only checking each ID first keeps it from the older
   * ID. */
  std::this_thread::sleep_for(std::chrono::milliseconds(2500));
  ID *newer = linked_id(lib, "Newer");
  /* Hashes the second version: cached and trusted. */
  ASSERT_TRUE(std::holds_alternative<ValidDeepHashes>(
      compute_linked_id_deep_hashes(*bmain, Span<const ID *>{newer})));

  const IDHashResult result = compute_linked_id_deep_hashes(*bmain, Span<const ID *>{older});
  ASSERT_TRUE(std::holds_alternative<DeepHashErrors>(result))
      << "an ID read from the first version got the second version's hash";
  EXPECT_TRUE(std::get<DeepHashErrors>(result).updated_files.contains(path.string()));
}

TEST_F(IDHashSourceFileTest, ALibraryThroughASymbolicLinkIsReadOncePerCallThenNotAgain)
{
  const fs::path version = folder / "v1.blend";
  const fs::path latest = folder / "latest.blend";
  write(version, "the asset library", std::chrono::seconds(10));
  std::error_code error;
  fs::create_symlink(version.filename(), latest, error);
  if (error) {
    GTEST_SKIP() << "no symbolic links here: " << error.message();
  }
  /* Its status change time (set with its modification time above) two seconds old too: a hash of
   * it can be trusted. */
  std::this_thread::sleep_for(std::chrono::milliseconds(2500));
  Library *lib = library(latest);
  Vector<const ID *> ids;
  for (const char *name : {"A", "B", "C", "D", "E"}) {
    ids.append(linked_id(lib, name));
  }

  const int64_t before = file_reads_for_tests();
  ASSERT_TRUE(std::holds_alternative<ValidDeepHashes>(compute_linked_id_deep_hashes(*bmain, ids)));
  EXPECT_EQ(file_reads_for_tests() - before, 1) << "five IDs of one file, one call";

  const int64_t again = file_reads_for_tests();
  ASSERT_TRUE(std::holds_alternative<ValidDeepHashes>(compute_linked_id_deep_hashes(*bmain, ids)));
  EXPECT_EQ(file_reads_for_tests() - again, 0) << "the file behind the link is unchanged";
}

}  // namespace blender::bke::id_hash::tests
