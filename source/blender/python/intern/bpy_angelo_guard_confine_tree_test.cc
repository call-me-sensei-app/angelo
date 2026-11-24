/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The moved-folder walk (bpy_angelo_guard_confine_tree.hh), on a real file system, on every system: a folder
 * holding a link at any depth is a link, a plain tree is plain, a larger tree than the limit or one that can't be
 * read is refused (break-it-confine-reparse-b7d499c head-7 M6).
 */

#include "testing/testing.h"

#include <chrono>
#include <cstdio>
#include <fstream>
#include <string>

#include "bpy_angelo_guard_confine_tree.hh"

#ifdef _WIN32
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#else
#  include <sys/stat.h>
#  include <unistd.h>
#  ifdef __APPLE__
#    include <cstring>
#    include <sys/mount.h>
#  endif
#endif

namespace blender::angelo_guard::tests {

namespace {

class ConfinementMoveTree : public ::testing::Test {
 protected:
  std::string root_;

  void SetUp() override
  {
#ifdef _WIN32
    char temp[MAX_PATH + 1];
    GetTempPathA(MAX_PATH, temp);
    root_ = std::string(temp) + "angelo-move-tree-" + std::to_string(GetCurrentProcessId()) + "-" +
            std::to_string(GetTickCount64());
#else
    const char *temp = getenv("TMPDIR");
    root_ = std::string(temp && temp[0] ? temp : "/tmp") + "/angelo-move-tree-" + std::to_string(getpid()) + "-" +
            std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
#endif
    ASSERT_TRUE(make_folder(root_));
  }

  void TearDown() override
  {
#ifdef _WIN32
    const std::string command = "cmd /c rmdir /s /q \"" + root_ + "\"";
#else
    chmod((root_ + "/tree/locked").c_str(), 0700);
    const std::string command = "rm -rf '" + root_ + "'";
#endif
    std::system(command.c_str());
  }

  static bool make_folder(const std::string &path)
  {
#ifdef _WIN32
    return CreateDirectoryA(path.c_str(), nullptr) != 0;
#else
    return mkdir(path.c_str(), 0700) == 0;
#endif
  }

  static void make_file(const std::string &path)
  {
    std::ofstream(path) << "x";
  }

  /** A folder link at `at` to `target` (Windows: a symbolic link if this account may, else false). */
  static bool make_link(const std::string &at, const std::string &target)
  {
#ifdef _WIN32
    return CreateSymbolicLinkA(at.c_str(), target.c_str(),
                               SYMBOLIC_LINK_FLAG_DIRECTORY | SYMBOLIC_LINK_FLAG_ALLOW_UNPRIVILEGED_CREATE) != 0;
#else
    return symlink(target.c_str(), at.c_str()) == 0;
#endif
  }

  std::string tree(const int depth)
  {
    std::string folder = root_ + "/tree";
    make_folder(folder);
    for (int i = 0; i < depth; i++) {
      make_file(folder + "/f" + std::to_string(i) + ".txt");
      folder += "/d" + std::to_string(i);
      make_folder(folder);
    }
    return folder;
  }
};

}  // namespace

TEST_F(ConfinementMoveTree, a_plain_tree_is_plain_and_a_file_or_nothing_too)
{
  tree(6);
  size_t entries = 0;
  EXPECT_EQ(move_tree_verdict(root_ + "/tree", -1, MOVE_TREE_LIMIT, &entries), TreeVerdict::Plain);
  EXPECT_EQ(entries, size_t(12)); /* Six files, six folders. */
  make_file(root_ + "/file.txt");
  EXPECT_EQ(move_tree_verdict(root_ + "/file.txt", -1), TreeVerdict::Plain);
  EXPECT_EQ(move_tree_verdict(root_ + "/nothing-here", -1), TreeVerdict::Plain);
}

TEST_F(ConfinementMoveTree, a_link_at_any_depth_or_the_folder_itself_is_a_link)
{
  const std::string deep = tree(5);
  make_folder(root_ + "/elsewhere");
  if (!make_link(deep + "/to-elsewhere", root_ + "/elsewhere")) {
    GTEST_SKIP() << "this account may not make symbolic links";
  }
  EXPECT_EQ(move_tree_verdict(root_ + "/tree", -1), TreeVerdict::Link);
  EXPECT_EQ(move_tree_verdict(deep, -1), TreeVerdict::Link);
  EXPECT_EQ(move_tree_verdict(deep + "/to-elsewhere", -1), TreeVerdict::Link); /* The link itself. */
  EXPECT_EQ(move_tree_verdict(root_ + "/elsewhere", -1), TreeVerdict::Plain);  /* Its target holds none. */
}

TEST_F(ConfinementMoveTree, a_tree_nested_deeper_than_the_limit_is_refused)
{
  /* Each open folder holds a descriptor (POSIX), so depth is capped at 128; Windows walks without descriptors but
   * the tree is refused the same way only on POSIX. */
  tree(130);
#ifdef _WIN32
  EXPECT_EQ(move_tree_verdict(root_ + "/tree", -1), TreeVerdict::Plain);
#else
  EXPECT_EQ(move_tree_verdict(root_ + "/tree", -1), TreeVerdict::TooLarge);
  EXPECT_EQ(move_tree_verdict(root_ + "/tree/d0/d1/d2/d3/d4", -1), TreeVerdict::Plain); /* 125 deep: walked. */
#endif
}

TEST_F(ConfinementMoveTree, more_entries_than_the_limit_are_refused)
{
  tree(6);
  EXPECT_EQ(move_tree_verdict(root_ + "/tree", -1, 11), TreeVerdict::TooLarge);
  EXPECT_EQ(move_tree_verdict(root_ + "/tree", -1, 12), TreeVerdict::Plain);
}

#ifndef _WIN32
TEST_F(ConfinementMoveTree, a_folder_that_cant_be_read_is_refused)
{
  tree(2);
  make_folder(root_ + "/tree/locked");
  make_file(root_ + "/tree/locked/x.txt");
  chmod((root_ + "/tree/locked").c_str(), 0);
  if (geteuid() == 0) {
    GTEST_SKIP() << "root reads every folder";
  }
  EXPECT_EQ(move_tree_verdict(root_ + "/tree", -1), TreeVerdict::Unreadable);
}
#endif

#ifdef __APPLE__
TEST_F(ConfinementMoveTree, a_folder_holding_an_autofs_trigger_is_refused_by_name)
{
  /* Head-12 re-read SHOULD: macOS has no AT_NO_AUTOMOUNT; a folder above a trigger the mount table lists (auto_home
   * on /System/Volumes/Data/home on a default Mac) is refused before anything below it is looked at. */
  struct statfs *mounts = nullptr;
  const int count = getmntinfo_r_np(&mounts, MNT_NOWAIT);
  std::string trigger;
  for (int i = 0; i < count && trigger.empty(); i++) {
    if (strcmp(mounts[i].f_fstypename, "autofs") == 0) {
      trigger = mounts[i].f_mntonname;
    }
  }
  free(mounts);
  if (trigger.empty() || trigger.find_last_of('/') == 0) {
    GTEST_SKIP() << "no autofs trigger below a folder here";
  }
  const std::string parent = trigger.substr(0, trigger.find_last_of('/'));
  size_t entries = 99;
  EXPECT_EQ(move_tree_verdict(parent, -1, MOVE_TREE_LIMIT, &entries), TreeVerdict::Link) << parent;
  EXPECT_EQ(entries, size_t(0)) << "refused by name, before anything below it was looked at";
  EXPECT_EQ(move_tree_verdict(root_ + "/nothing-here", -1), TreeVerdict::Plain);
}
#endif

TEST_F(ConfinementMoveTree, what_a_walk_costs)
{
  /* The limit's basis: entries per millisecond on this machine (printed for the report). */
  const std::string folder = root_ + "/wide";
  make_folder(folder);
  for (int sub = 0; sub < 20; sub++) {
    const std::string inner = folder + "/s" + std::to_string(sub);
    make_folder(inner);
    for (int i = 0; i < 500; i++) {
      make_file(inner + "/f" + std::to_string(i));
    }
  }
  size_t entries = 0;
  const auto started = std::chrono::steady_clock::now();
  EXPECT_EQ(move_tree_verdict(folder, -1, MOVE_TREE_LIMIT, &entries), TreeVerdict::Plain);
  const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
  std::printf("[ cost ] moved-folder walk: %zu entries in %.2f ms (%.0f entries/ms)\n", entries, ms,
              ms > 0 ? double(entries) / ms : 0.0);
  EXPECT_EQ(entries, size_t(10020));
}

}  // namespace blender::angelo_guard::tests
