/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * A durable save (#BlendFileWriteParams.use_durable: Save, auto-save, the startup file,
 * preferences) syncs its temporary file before renaming it over the old one, then the folder, so
 * a power loss right after it cannot leave a truncated file (Blender #146697, #157476, #151813).
 * Other writes skip the cost. A sync that reports lost data fails the save and keeps the old
 * file; a file system that cannot sync saves as before.
 *
 * The syncs go through #BLO_write_sync_hook_set here, which records them and injects failures.
 */

#include <cerrno>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include "DNA_scene_types.h"

#include "BLI_fileops.h"
#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "BLI_tempfile.h"

#include "BKE_global.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"

#include "BLO_readfile.hh"
#include "BLO_writefile.hh"

#include "blendfile_loading_base_test.h"

namespace blender {
namespace {

struct SyncCall {
  /** The descriptor for the temporary file's sync, -1 for the folder's. */
  int file;
  std::string dirpath;
  /** Size of the temporary file at its sync (what the rename will publish). */
  int64_t size;
};

std::vector<SyncCall> sync_calls;
/** errno to fail the file sync with, or 0. */
int fail_file_sync = 0;
/** errno to fail the folder sync with, or 0. */
int fail_dir_sync = 0;

/** errno values that mean written data may be lost (`EDQUOT` is POSIX only). */
const std::vector<int> &lost_data_errors()
{
  static const std::vector<int> errors = {EIO,
                                          ENOSPC,
#ifdef EDQUOT
                                          EDQUOT,
#endif
                                          ETIMEDOUT};
  return errors;
}

int record_sync(const int file, const char *dirpath)
{
  SyncCall call{file, dirpath ? dirpath : "", -1};
  if (file != -1) {
    BLI_stat_t st;
    if (BLI_fstat(file, &st) == 0) {
      call.size = int64_t(st.st_size);
    }
  }
  sync_calls.push_back(call);
  const int error = (file != -1) ? fail_file_sync : fail_dir_sync;
  if (error) {
    errno = error;
    return -1;
  }
  return 0;
}

class WritefileDurableTest : public BlendfileLoadingBaseTest {
 protected:
  Main *main_ = nullptr;
  ReportList reports_{};
  std::string directory_;

  void SetUp() override
  {
    BKE_reports_init(&reports_, RPT_STORE | RPT_PRINT_HANDLED_BY_OWNER);
    char temporary[FILE_MAX];
    BLI_temp_directory_path_get(temporary, sizeof(temporary));
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    char candidate[FILE_MAX];
    BLI_path_join(candidate,
                  sizeof(candidate),
                  temporary,
                  ("blender_durable_save_test_" + std::to_string(nonce)).c_str());
    ASSERT_TRUE(BLI_dir_create_recursive(candidate));
    directory_ = candidate;

    main_ = BKE_main_new();
    ASSERT_NE(main_, nullptr);
    ASSERT_NE(BKE_scene_add(main_, "DurableFixture"), nullptr);

    sync_calls.clear();
    fail_file_sync = 0;
    fail_dir_sync = 0;
    BLO_write_sync_hook_set(record_sync);
  }

  void TearDown() override
  {
    BLO_write_sync_hook_set(nullptr);
    if (main_) {
      BKE_main_free(main_);
    }
    BKE_reports_free(&reports_);
    if (!directory_.empty()) {
      std::error_code error;
      std::filesystem::remove_all(
          std::filesystem::path(std::u8string(directory_.begin(), directory_.end())), error);
    }
    BlendfileLoadingBaseTest::TearDown();
  }

  std::string path(const char *name) const
  {
    char result[FILE_MAX];
    BLI_path_join(result, sizeof(result), directory_.c_str(), name);
    return result;
  }

  std::string folder() const
  {
    char result[FILE_MAX];
    BLI_path_split_dir_part(path("x.blend").c_str(), result, sizeof(result));
    return result;
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

  bool save(const std::string &target, const bool durable, const int flags = 0)
  {
    BlendFileWriteParams params{};
    params.use_save_as_copy = true;
    params.use_durable = durable;
    BKE_reports_clear(&reports_);
    return BLO_write_file(main_, target.c_str(), flags, &params, &reports_);
  }

  void expect_readable(const std::string &filepath)
  {
    BlendFileReadReport read_reports{};
    read_reports.reports = &reports_;
    BlendFileData *loaded = BLO_read_from_file(
        filepath.c_str(), BLO_READ_SKIP_NONE, &read_reports);
    ASSERT_NE(loaded, nullptr);
    EXPECT_NE(BKE_libblock_find_name(loaded->main, ID_SCE, "DurableFixture"), nullptr);
    BLO_blendfiledata_free(loaded);
  }
};

TEST_F(WritefileDurableTest, DurableSaveSyncsTheWholeFileThenItsFolder)
{
  for (const int flags : {0, int(G_FILE_COMPRESS)}) {
    sync_calls.clear();
    const std::string target = path(flags ? "compressed.blend" : "plain.blend");
    ASSERT_TRUE(save(target, true, flags));
    ASSERT_EQ(sync_calls.size(), 2u) << "flags " << flags;
    EXPECT_NE(sync_calls[0].file, -1);
    /* The data synced is all of the file the rename published. */
    EXPECT_EQ(sync_calls[0].size, int64_t(BLI_file_size(target.c_str())));
    EXPECT_EQ(sync_calls[1].file, -1);
    EXPECT_EQ(sync_calls[1].dirpath, folder());
    expect_readable(target);
  }
}

TEST_F(WritefileDurableTest, OtherWritesDoNotSync)
{
  ASSERT_TRUE(save(path("copy.blend"), false));
  ASSERT_TRUE(save(path("copy_compressed.blend"), false, G_FILE_COMPRESS));
  EXPECT_TRUE(sync_calls.empty());
}

TEST_F(WritefileDurableTest, LostDataFailsTheSaveAndKeepsTheOldFile)
{
  const std::string target = path("kept.blend");
  const std::string old_bytes = "the previous version";
  /* An I/O error, a full disk or quota and a network timeout all mean written data may be lost
   * (an NFS client reports a failed write-back once, so the save must not count it as synced). */
  for (const int error : lost_data_errors()) {
    write_bytes(target, old_bytes);
    fail_file_sync = error;
    EXPECT_FALSE(save(target, true)) << strerror(error);
    EXPECT_EQ(read_bytes(target), old_bytes) << strerror(error);
    EXPECT_FALSE(BLI_exists((target + "@").c_str())) << strerror(error);
  }
}

TEST_F(WritefileDurableTest, FileSystemsThatCannotSyncStillSave)
{
  for (const int error : {EINVAL, ENOTSUP, EOPNOTSUPP, ENOSYS, ENOTTY}) {
    EXPECT_TRUE(BLI_file_sync_error_is_unsupported(error)) << strerror(error);
    const std::string target = path("unsynced.blend");
    write_bytes(target, "old");
    fail_file_sync = error;
    EXPECT_TRUE(save(target, true)) << strerror(error);
    expect_readable(target);
  }
  for (const int error : lost_data_errors()) {
    EXPECT_FALSE(BLI_file_sync_error_is_unsupported(error)) << strerror(error);
  }
  EXPECT_FALSE(BLI_file_sync_error_is_unsupported(EBADF));
}

TEST_F(WritefileDurableTest, FolderSyncFailureKeepsTheSave)
{
  /* The data was ordered before the rename, so at worst a power loss brings back the previous
   * version: the save stands, with a warning. */
  const std::string target = path("folder.blend");
  fail_dir_sync = EIO;
  EXPECT_TRUE(save(target, true));
  expect_readable(target);
}

}  // namespace
}  // namespace blender
