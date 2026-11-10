/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Angelo: a save that identifies the file it wrote (#BlendFileWriteParams::written) never renames
 * another program's file into the document's place, and never removes one. The temporary file
 * `<file>@` is a predictable name: another program can replace it, or move it aside, while it is
 * written. The swap runs from the file sync hook (#BLO_write_sync_hook_set), which a durable save
 * calls on the finished temporary file just before its writer closes it.
 */

#include <cerrno>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <string>

#include "DNA_scene_types.h"

#include "MEM_guardedalloc.h"

#include "BLI_fileops.h"
#include "BLI_fileops.hh"
#include "BLI_path_utils.hh"
#include "BLI_tempfile.h"

#include "BKE_main.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"

#include "BLO_writefile.hh"

#include "blendfile_loading_base_test.h"

namespace blender {
namespace {

/** What the file sync hook does to the temporary file, then the errno it fails the sync with. */
std::function<int()> at_sync;
/** What the stage hook does at a stage (#BLO_write_stage_hook_set): 0 after the hold, 1 before
 * the rename. */
std::function<void(int)> at_stage;

void run_at_stage(const int stage, const char * /*tempname*/)
{
  if (at_stage) {
    at_stage(stage);
  }
}

int run_at_sync(const int file, const char * /*dirpath*/)
{
  const int error = (file != -1 && at_sync) ? at_sync() : 0;
  if (error) {
    errno = error;
    return -1;
  }
  return 0;
}

class WritefileIdentityTest : public BlendfileLoadingBaseTest {
 protected:
  Main *main_ = nullptr;
  ReportList reports_{};
  std::filesystem::path directory_;

  void SetUp() override
  {
    BKE_reports_init(&reports_, RPT_STORE | RPT_PRINT_HANDLED_BY_OWNER);
    char temporary[FILE_MAX];
    BLI_temp_directory_path_get(temporary, sizeof(temporary));
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    directory_ = std::filesystem::path(temporary) /
                 ("blender_identity_save_test_" + std::to_string(nonce));
    std::filesystem::create_directories(directory_);
    main_ = BKE_main_new();
    ASSERT_NE(main_, nullptr);
    ASSERT_NE(BKE_scene_add(main_, "IdentityFixture"), nullptr);
    at_sync = nullptr;
    at_stage = nullptr;
    BLO_write_sync_hook_set(run_at_sync);
    BLO_write_stage_hook_set(run_at_stage);
  }

  void TearDown() override
  {
    BLO_write_sync_hook_set(nullptr);
    BLO_write_stage_hook_set(nullptr);
    at_sync = nullptr;
    at_stage = nullptr;
    if (main_) {
      BKE_main_free(main_);
    }
    BKE_reports_free(&reports_);
    std::error_code error;
    std::filesystem::remove_all(directory_, error);
    BlendfileLoadingBaseTest::TearDown();
  }

  std::string path(const char *name) const
  {
    return (directory_ / name).string();
  }

  static std::string read_bytes(const std::string &filepath)
  {
    std::ifstream stream(filepath, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
  }

  static void write_bytes(const std::string &filepath, const std::string &bytes)
  {
    std::ofstream stream(filepath, std::ios::binary | std::ios::trunc);
    stream << bytes;
  }

  /** A durable, identified save with version backups, as the GUI's Save. */
  bool save(const std::string &target, FileIdentity *r_written, bool *r_valid)
  {
    BlendFileWriteParams params{};
    params.use_save_as_copy = true;
    params.use_save_versions = true;
    params.use_durable = true;
    params.written = r_written;
    params.written_valid = r_valid;
    BKE_reports_clear(&reports_);
    return BLO_write_file(main_, target.c_str(), 0, &params, &reports_);
  }

  bool reported(const char *text)
  {
    char *all = BKE_reports_string(&reports_, RPT_INFO);
    const bool found = all != nullptr && std::string(all).find(text) != std::string::npos;
    MEM_delete(all);
    return found;
  }
};

TEST_F(WritefileIdentityTest, AnIdentifiedSaveNamesTheFileAtItsPath)
{
  const std::string target = path("document.blend");
  FileIdentity written, at;
  bool valid = false;
  ASSERT_TRUE(save(target, &written, &valid));
  EXPECT_TRUE(valid);
  ASSERT_TRUE(BLI_file_identity(target.c_str(), &at));
  EXPECT_TRUE(BLI_file_identity_same_file(written, at));
}

/* POSIX: on Windows the writer's handle shares no delete, so nothing replaces or moves the
 * temporary file while it is written. */
#ifndef WIN32
TEST_F(WritefileIdentityTest, ATemporaryFileReplacedDuringTheWriteIsRefusedAndChangesNothing)
{
  const std::string target = path("document.blend"), temporary = target + "@";
  write_bytes(target, "the previous version");
  at_sync = [&]() {
    write_bytes(path("theirs"), "another program's file");
    std::filesystem::rename(path("theirs"), temporary);
    return 0;
  };
  FileIdentity written;
  bool valid = true;
  EXPECT_FALSE(save(target, &written, &valid));
  EXPECT_FALSE(valid);
  EXPECT_TRUE(reported("another program replaced the file being saved"));
  EXPECT_EQ(read_bytes(target), "the previous version");
  EXPECT_FALSE(BLI_exists((target + "1").c_str())) << "refused before the version backups";
}

TEST_F(WritefileIdentityTest, ATemporaryFileMovedAsideThenReplacedIsRefused)
{
  const std::string target = path("document.blend"), temporary = target + "@";
  write_bytes(target, "the previous version");
  at_sync = [&]() {
    std::filesystem::rename(temporary, path("document conflicted copy.blend"));
    write_bytes(temporary, "another program's file");
    return 0;
  };
  FileIdentity written;
  bool valid = true;
  EXPECT_FALSE(save(target, &written, &valid));
  EXPECT_EQ(read_bytes(target), "the previous version");
  EXPECT_EQ(read_bytes(temporary), "another program's file") << "never removed";
}

TEST_F(WritefileIdentityTest, ATemporaryFileMovedAsideIsRefusedBeforeTheVersionBackups)
{
  /* Before: the save went on, moved the document to its backup and then failed to rename a
   * temporary file that was gone, leaving no file at the document's name. */
  const std::string target = path("document.blend"), temporary = target + "@";
  write_bytes(target, "the previous version");
  at_sync = [&]() {
    std::filesystem::rename(temporary, path("aside.blend"));
    return 0;
  };
  FileIdentity written;
  bool valid = true;
  EXPECT_FALSE(save(target, &written, &valid));
  EXPECT_EQ(read_bytes(target), "the previous version");
  EXPECT_FALSE(BLI_exists((target + "1").c_str()));
}
TEST_F(WritefileIdentityTest, AFailedWriteNeverRemovesAnotherProgramsFileAtTheTemporaryName)
{
  /* Before: a failed write removed `<file>@` by its name, whatever file was there by then. */
  const std::string target = path("document.blend"), temporary = target + "@";
  write_bytes(target, "the previous version");
  at_sync = [&]() {
    write_bytes(path("theirs"), "another program's file");
    std::filesystem::rename(path("theirs"), temporary);
    return EIO;
  };
  FileIdentity written;
  bool valid = true;
  EXPECT_FALSE(save(target, &written, &valid));
  EXPECT_EQ(read_bytes(target), "the previous version");
  EXPECT_EQ(read_bytes(temporary), "another program's file");
}

TEST_F(WritefileIdentityTest, AReplaceAfterTheHoldIsRefusedBeforeTheBackups)
{
  /* Before: the check compared only the hold with the writer, so a file renamed over `<file>@`
   * after the hold was renamed into the document's place and the save said it saved. */
  const std::string target = path("document.blend"), temporary = target + "@";
  write_bytes(target, "the previous version");
  at_stage = [&](const int stage) {
    if (stage == 0) {
      write_bytes(path("theirs"), "another program's file");
      std::filesystem::rename(path("theirs"), temporary);
    }
  };
  FileIdentity written;
  bool valid = true;
  EXPECT_FALSE(save(target, &written, &valid));
  EXPECT_FALSE(valid);
  EXPECT_EQ(read_bytes(target), "the previous version");
  EXPECT_FALSE(BLI_exists((target + "1").c_str()));
  EXPECT_EQ(read_bytes(temporary), "another program's file");
}

TEST_F(WritefileIdentityTest, AMoveAsideAfterTheHoldIsRefusedBeforeTheBackups)
{
  const std::string target = path("document.blend"), temporary = target + "@";
  write_bytes(target, "the previous version");
  at_stage = [&](const int stage) {
    if (stage == 0) {
      std::filesystem::rename(temporary, path("aside.blend"));
    }
  };
  FileIdentity written;
  bool valid = true;
  EXPECT_FALSE(save(target, &written, &valid));
  EXPECT_EQ(read_bytes(target), "the previous version");
  EXPECT_FALSE(BLI_exists((target + "1").c_str()));
}

TEST_F(WritefileIdentityTest, AReplaceJustBeforeTheRenameWarnsAndNamesNoFile)
{
  /* The moment between the check and the rename: the rename puts the other file in place. The
   * save can't take that back; it names no file and says so. Before: named, and silent. */
  const std::string target = path("document.blend"), temporary = target + "@";
  write_bytes(target, "the previous version");
  at_stage = [&](const int stage) {
    if (stage == 1) {
      write_bytes(path("theirs"), "another program's file");
      std::filesystem::rename(path("theirs"), temporary);
    }
  };
  FileIdentity written;
  bool valid = true;
  EXPECT_TRUE(save(target, &written, &valid));
  EXPECT_FALSE(valid);
  EXPECT_TRUE(reported("the file on disk may be that program's"));
  EXPECT_EQ(read_bytes(target), "another program's file");
  EXPECT_EQ(read_bytes(target + "1"), "the previous version") << "the version backup keeps it";
}
#endif

TEST_F(WritefileIdentityTest, AFailedWriteStillRemovesItsOwnTemporaryFile)
{
  const std::string target = path("document.blend");
  write_bytes(target, "the previous version");
  at_sync = []() { return EIO; };
  FileIdentity written;
  bool valid = true;
  EXPECT_FALSE(save(target, &written, &valid));
  EXPECT_EQ(read_bytes(target), "the previous version");
  EXPECT_FALSE(BLI_exists((target + "@").c_str()));
}

}  // namespace
}  // namespace blender
