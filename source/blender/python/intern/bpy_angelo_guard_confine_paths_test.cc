/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Where a path really is, for the agent-script confinement (`bpy_angelo_guard_confine_paths`): every alias of a
 * file in a private folder is that folder's (a link to the file, a link to the folder, `..`, a folder descriptor,
 * `/dev/fd`, case on macOS and Windows), a new file inside a private folder is private, a startup folder refuses
 * changes but not reads, a link may not point into or above a private folder, a family's members are private, and
 * an ordinary folder is free.
 */

#include "testing/testing.h"

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <string>

#ifndef _WIN32
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#endif

#include "BLI_fileops.hh"
#include "BLI_path_utils.hh"
#include "BLI_tempfile.hh"

#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard {
/* `bpy_angelo_guard_confine_writable.cc` (declared in the internal header, which needs Python's). */
bool vouch(const std::string &path, bool folder, bool persist);
}  // namespace blender::angelo_guard

namespace blender::angelo_guard::tests {

class ConfinementPathsTest : public testing::Test {
 protected:
  std::string root_, secret_, plain_, startup_, tool_;
  Folders folders_;

  void SetUp() override
  {
    char temp[1024];
    BLI_temp_directory_path_get(temp, sizeof(temp));
    std::string base(temp);
    if (!base.empty() && base.back() != '/' && base.back() != '\\') {
      base += '/';
    }
    root_ = base + "angelo-confine-paths-" + std::to_string(std::rand());
    secret_ = root_ + "/State Folder";
    plain_ = root_ + "/plain";
    startup_ = root_ + "/startup";
    tool_ = root_ + "/angelo-downloads";
    BLI_dir_create_recursive((secret_ + "/keys").c_str());
    BLI_dir_create_recursive(plain_.c_str());
    BLI_dir_create_recursive(startup_.c_str());
    BLI_dir_create_recursive(tool_.c_str());
    std::ofstream(secret_ + "/keys/provider.key") << "sk-original";
    folders_.private_roots.add(secret_);
    folders_.startup_roots.add(startup_);
    folders_.tool_roots.add(tool_);
    folders_.families.add(root_, "angelo-worker-");
  }

  void TearDown() override
  {
    BLI_delete(root_.c_str(), true, true);
  }

  Refusal judge(const std::string &path, bool write = false, int dir_fd = -1, bool link = false)
  {
    return folders_.judge(path, write, dir_fd, link);
  }
};

TEST_F(ConfinementPathsTest, direct_and_new_files)
{
  EXPECT_EQ(judge(secret_ + "/keys/provider.key"), Refusal::Private);
  EXPECT_EQ(judge(secret_ + "/keys/new.json", true), Refusal::Private);
  EXPECT_EQ(judge(secret_ + "/not/yet/made.json", true), Refusal::Private);
  EXPECT_EQ(judge(secret_), Refusal::Private);
  EXPECT_EQ(judge(plain_ + "/scene.png", true), Refusal::None);
  EXPECT_EQ(judge(root_ + "/State Folder Copy/x", true), Refusal::None); /* A sibling sharing a prefix. */
}

TEST_F(ConfinementPathsTest, dotdot)
{
  EXPECT_EQ(judge(plain_ + "/../State Folder/keys/provider.key"), Refusal::Private);
  EXPECT_EQ(judge(secret_ + "/keys/missing/../provider.key"), Refusal::Private);
}

TEST_F(ConfinementPathsTest, startup_folders_refuse_changes_only)
{
  EXPECT_EQ(judge(startup_ + "/evil.py", true), Refusal::Startup);
  EXPECT_EQ(judge(startup_ + "/readme.txt", false), Refusal::None);
}

TEST_F(ConfinementPathsTest, tool_output_folders_are_read_never_changed_or_trusted)
{
  EXPECT_EQ(judge(tool_ + "/chair.glb", false), Refusal::None);
  EXPECT_EQ(judge(tool_ + "/chair.glb", true), Refusal::ToolOutput);
  EXPECT_EQ(judge(tool_ + "/new/folder/x.py", true), Refusal::ToolOutput);
  EXPECT_EQ(judge(plain_ + "/../angelo-downloads/x", true), Refusal::ToolOutput);
  EXPECT_EQ(judge(tool_ + "/chair.glb", false, -1, true), Refusal::ToolOutput); /* A new link's target. */
  EXPECT_EQ(judge(root_, false, -1, true), Refusal::Private);                   /* A link above it. */
  EXPECT_FALSE(folders_.trusted(tool_ + "/evil.py"));
}

TEST_F(ConfinementPathsTest, an_open_folder_inside_a_private_one_is_used_freely_but_not_below)
{
  const std::string chat = secret_ + "/chats/c1";
  const std::string workspace = chat + "/workspace";
  BLI_dir_create_recursive((workspace + "/claude-profile").c_str());
  BLI_dir_create_recursive((workspace + "/inner").c_str());
  std::ofstream(workspace + "/notes.json") << "{}";
  folders_.open_private_names.add("claude-profile");
  folders_.open_roots.add(workspace);
  folders_.private_roots.add(workspace + "/inner"); /* A private folder registered inside it. */
  EXPECT_EQ(judge(workspace + "/notes.json"), Refusal::None);
  EXPECT_EQ(judge(workspace + "/notes.json", true), Refusal::None);
  EXPECT_EQ(judge(workspace + "/new/deeper/file.txt", true), Refusal::None);
  EXPECT_EQ(judge(workspace), Refusal::None);
  EXPECT_EQ(judge(workspace + "/claude-profile/auth.json"), Refusal::Private);
  EXPECT_EQ(judge(workspace + "/inner/x"), Refusal::Private);
  EXPECT_EQ(judge(chat + "/session.json"), Refusal::Private);   /* Beside it: still the private folder. */
  EXPECT_EQ(judge(workspace + "/../session.json"), Refusal::Private);
  EXPECT_EQ(judge(secret_ + "/keys/provider.key", false, -1, true), Refusal::Private); /* A link out of it. */
}

TEST_F(ConfinementPathsTest, a_private_folder_that_is_the_open_folder_stays_private)
{
  const std::string workspace = secret_ + "/chats/c2/workspace";
  BLI_dir_create_recursive(workspace.c_str());
  folders_.open_roots.add(workspace);
  folders_.private_roots.add(workspace);
  EXPECT_EQ(judge(workspace + "/x.json"), Refusal::Private);
}

TEST_F(ConfinementPathsTest, families)
{
  EXPECT_EQ(judge(root_ + "/angelo-worker-abc123/state/x.json"), Refusal::Private);
  EXPECT_EQ(judge(root_ + "/angelo-workers.txt"), Refusal::None);
}

#ifndef _WIN32
TEST_F(ConfinementPathsTest, links)
{
  ASSERT_EQ(symlink((secret_ + "/keys/provider.key").c_str(), (plain_ + "/alias").c_str()), 0);
  ASSERT_EQ(symlink((secret_ + "/keys").c_str(), (plain_ + "/folder-link").c_str()), 0);
  ASSERT_EQ(symlink("../State Folder", (plain_ + "/relative-link").c_str()), 0);
  EXPECT_EQ(judge(plain_ + "/alias"), Refusal::Private);
  EXPECT_EQ(judge(plain_ + "/folder-link/provider.key"), Refusal::Private);
  EXPECT_EQ(judge(plain_ + "/relative-link/keys/provider.key"), Refusal::Private);
  /* A new link may not point into a private folder, nor above one. */
  EXPECT_EQ(judge(secret_ + "/keys/provider.key", false, -1, true), Refusal::Private);
  EXPECT_EQ(judge(root_, false, -1, true), Refusal::Private);
  EXPECT_EQ(judge(plain_ + "/scene.png", false, -1, true), Refusal::None);
}

TEST_F(ConfinementPathsTest, descriptors)
{
  const int folder = open((secret_ + "/keys").c_str(), O_RDONLY);
  ASSERT_GE(folder, 0);
  EXPECT_EQ(judge("provider.key", false, folder), Refusal::Private);
  const int file = open((secret_ + "/keys/provider.key").c_str(), O_RDONLY);
  ASSERT_GE(file, 0);
#  ifdef __APPLE__
  EXPECT_EQ(judge("/dev/fd/" + std::to_string(file)), Refusal::Private);
#  else
  EXPECT_EQ(judge("/proc/self/fd/" + std::to_string(file)), Refusal::Private);
#  endif
  const std::optional<std::string> path = descriptor_path(file);
  ASSERT_TRUE(path.has_value());
  EXPECT_EQ(folders_.judge(*path, false), Refusal::Private);
  close(file);
  close(folder);
}

TEST_F(ConfinementPathsTest, working_folder)
{
  char before[4096];
  ASSERT_NE(getcwd(before, sizeof(before)), nullptr);
  ASSERT_EQ(chdir((secret_ + "/keys").c_str()), 0);
  EXPECT_EQ(judge("provider.key"), Refusal::Private);
  ASSERT_EQ(chdir(before), 0);
}
#endif

#if defined(__APPLE__) || defined(_WIN32)
TEST_F(ConfinementPathsTest, case)
{
  std::string other = secret_;
  for (char &c : other) {
    c = char(std::toupper(c));
  }
  EXPECT_EQ(judge(other + "/KEYS/PROVIDER.KEY"), Refusal::Private);
}
#endif

TEST_F(ConfinementPathsTest, a_root_added_twice_and_resolved_is_one)
{
  folders_.private_roots.add(secret_ + "/");
  folders_.private_roots.add(plain_ + "/../State Folder");
  EXPECT_EQ(folders_.private_roots.size(), 1u);
}

/* A vouch naming a parent folder (`..`, either separator, anywhere) is refused before anything is recorded: one that
 * came from a module value changed in place would otherwise land on a folder of the person's (p10c-3-library). */
TEST(ConfinementVouchTest, a_path_naming_a_parent_folder_is_never_vouched_for)
{
  for (const char *path : {"/tmp/document/../..", "/tmp/a/../b/assets", "..", "../x", "C:\\x\\..\\y",
                           "/tmp/a/..\\b"})
  {
    EXPECT_FALSE(vouch(path, true, false)) << path;
    EXPECT_FALSE(vouch(path, false, false)) << path;
  }
}

TEST(ConfinementRemoteNames, a_remote_name_is_known_by_its_text)
{
#ifdef _WIN32
  for (const char *remote : {"\\\\host\\share\\x", "//host/share/x", "\\\\?\\UNC\\host\\share\\x",
                             "\\??\\UNC\\host\\share\\x", "\\\\.\\UNC\\host\\x", "\\\\host@SSL\\DavWWWRoot\\x",
                             "\\\\.\\pipe\\name", "\\\\?\\GLOBALROOT\\Device\\x", "/\\host\\x"})
  {
    EXPECT_TRUE(names_a_remote_file(remote)) << remote;
  }
  for (const char *local : {"C:\\x", "c:/x", "\\\\?\\C:\\x", "\\\\.\\D:\\x", "\\??\\C:\\x", "relative\\x", "\\x"}) {
    EXPECT_FALSE(names_a_remote_file(local)) << local;
  }
#else
  for (const char *local : {"//host/share/x", "/x", "relative/x"}) {
    EXPECT_FALSE(names_a_remote_file(local)) << local;
  }
#endif
}

}  // namespace blender::angelo_guard::tests
