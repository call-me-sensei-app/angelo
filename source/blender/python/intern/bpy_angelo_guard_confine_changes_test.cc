/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The record of what scripts changed (`bpy_angelo_guard_confine_changes`): files, never ancestry; a folder only
 * moved, linked or filled past `COMPACT_CHILDREN`; a rename or copy elsewhere still the script's; a record whose
 * volume is away kept; forgetting a folder; failing closed.
 */

#include "testing/testing.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <thread>

#include "BLI_fileops.hh"
#include "BLI_tempfile.hh"

#include "bpy_angelo_guard_confine_changes.hh"
#include "bpy_angelo_guard_confine_paths.hh"

namespace blender::angelo_guard::tests {

class ConfinementChangesTest : public testing::Test {
 protected:
  std::string root_, secret_, plain_, trusted_, record_;
  Folders folders_;

  void SetUp() override
  {
    char temp[1024];
    BLI_temp_directory_path_get(temp, sizeof(temp));
    std::string base(temp);
    if (!base.empty() && base.back() != '/' && base.back() != '\\') {
      base += '/';
    }
    root_ = *resolve(base) + "/angelo-confine-changes-" + std::to_string(std::rand());
    secret_ = root_ + "/State Folder";
    plain_ = root_ + "/plain";
    trusted_ = root_ + "/installed";
    BLI_dir_create_recursive(secret_.c_str());
    BLI_dir_create_recursive(plain_.c_str());
    BLI_dir_create_recursive(trusted_.c_str());
    folders_.private_roots.add(secret_);
    folders_.trusted_roots.add(trusted_);
    record_ = secret_ + "/angelo_agent_changed.txt";
    changes_record_set(record_);
  }

  void TearDown() override
  {
    changes_record_set("");
    changes_reset();
    BLI_delete(root_.c_str(), true, true);
  }

  /** A confined write of `text` to `path`, as the hook judges it and the file is then written. */
  void script_writes(const std::string &path, const std::string &text)
  {
    ASSERT_EQ(folders_.judge(path, true), Refusal::None);
    std::ofstream(path, std::ios::binary) << text;
  }
  void script_makes_folder(const std::string &path)
  {
    ASSERT_EQ(folders_.judge(path, true, -1, false, Change::Metadata), Refusal::None);
    BLI_dir_create_recursive(path.c_str());
  }
  static void person_writes(const std::string &path, const std::string &text)
  {
    std::ofstream(path, std::ios::binary) << text;
  }
  /** A path as C writes it in the record: the platform's own separator (a record line is never rewritten). */
  static std::string native(std::string path)
  {
#ifdef _WIN32
    for (char &c : path) {
      c = c == '/' ? '\\' : c;
    }
#endif
    return path;
  }
  bool made(const std::string &path)
  {
    return folders_.agent_made(path);
  }
  std::string record_text()
  {
    std::ifstream in(record_, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
  }
};

TEST_F(ConfinementChangesTest, files_not_ancestry)
{
  const std::string project = plain_ + "/project";
  script_makes_folder(project + "/addon");
  ASSERT_EQ(folders_.judge(project, true, -1, false, Change::Metadata), Refusal::None); /* chmod, utime */
  script_writes(project + "/agent_tool.py", "x = 1");
  changes_settle();
  person_writes(project + "/addon/__init__.py", "y = 22");
  person_writes(project + "/mine.py", "z = 333");
  EXPECT_TRUE(made(project + "/agent_tool.py"));
  EXPECT_FALSE(made(project + "/addon/__init__.py")); /* The person's add-on in a folder a script made. */
  EXPECT_FALSE(made(project + "/mine.py"));
  EXPECT_EQ(record_text().find(project + "/addon\n"), std::string::npos);
}

TEST_F(ConfinementChangesTest, moved_or_linked_folder_counts)
{
  const std::string from = plain_ + "/person-folder", to = plain_ + "/moved-folder";
  BLI_dir_create_recursive(from.c_str());
  person_writes(from + "/m.py", "q = 1");
  ASSERT_EQ(folders_.judge(to, true, -1, false, Change::Moved), Refusal::None);
  ASSERT_EQ(std::rename(from.c_str(), to.c_str()), 0);
  changes_settle();
  EXPECT_TRUE(made(to + "/m.py"));
  EXPECT_FALSE(made(plain_ + "/other.py"));
}

TEST_F(ConfinementChangesTest, compaction_bounds_the_record)
{
  const std::string many = plain_ + "/many";
  script_makes_folder(many);
  for (int i = 0; i < int(COMPACT_CHILDREN) + 44; i++) {
    script_writes(many + "/f" + std::to_string(i) + ".txt", "data " + std::to_string(i));
  }
  changes_settle();
  EXPECT_LE(changes_paths_count(), size_t(2));
  EXPECT_EQ(record_text().find(many + "/f0.txt"), std::string::npos); /* The file holds the compaction too. */
  person_writes(many + "/later.py", "w = 1");
  EXPECT_TRUE(made(many + "/later.py")); /* The folder a script filled is recorded whole. */
  /* A folder that holds one of Angelo's roots is never recorded whole. */
  for (int i = 0; i < int(COMPACT_CHILDREN) + 10; i++) {
    script_writes(root_ + "/top" + std::to_string(i) + ".txt", "t" + std::to_string(i));
  }
  changes_settle();
  person_writes(root_ + "/person.py", "p = 1");
  EXPECT_FALSE(made(root_ + "/person.py"));
}

TEST_F(ConfinementChangesTest, rename_and_copy_stay_the_scripts)
{
  const std::string written = plain_ + "/agent.py", big = plain_ + "/big.py";
  script_writes(written, "print('agent')\n");
  std::string bulk(3 * EDGE_BYTES + 17, 'a');
  bulk.back() = 'z';
  script_writes(big, bulk);
  changes_settle();
  const std::string elsewhere = root_ + "/elsewhere";
  BLI_dir_create_recursive(elsewhere.c_str());
  ASSERT_EQ(std::rename(written.c_str(), (elsewhere + "/renamed.py").c_str()), 0); /* Unconfined: unrecorded. */
  EXPECT_TRUE(made(elsewhere + "/renamed.py"));                                     /* By identity. */
  person_writes(elsewhere + "/copy.py", "print('agent')\n");
  EXPECT_TRUE(made(elsewhere + "/copy.py")); /* By content. */
  person_writes(elsewhere + "/same_size.py", "print('human')\n");
  EXPECT_FALSE(made(elsewhere + "/same_size.py"));
  person_writes(elsewhere + "/big_copy.py", bulk);
  EXPECT_TRUE(made(elsewhere + "/big_copy.py"));
  std::string other = bulk;
  other.back() = 'y';
  person_writes(elsewhere + "/big_other.py", other);
  EXPECT_FALSE(made(elsewhere + "/big_other.py"));
  person_writes(trusted_ + "/copy.py", "print('agent')\n"); /* Installed code is installed, whatever its bytes. */
  EXPECT_FALSE(made(trusted_ + "/copy.py"));
  /* A later session still knows the content of a file renamed away. */
  changes_record_set(record_);
  EXPECT_TRUE(made(elsewhere + "/copy.py"));
}

TEST_F(ConfinementChangesTest, pruning_keeps_a_missing_volume)
{
  const std::optional<Identity> here = entry_identity(plain_);
  ASSERT_TRUE(here.has_value());
  /* A record line of the current format holds the key C writes (resolved; on Windows and macOS lowercased, with the
   * platform's separator): the test writes those lines by hand below. The earlier format is re-keyed on load. */
  const auto recorded = [](const std::string &path) { return key(*resolve(path)); };
  const std::string away = recorded(plain_ + "/away/x.py"), gone = recorded(plain_ + "/gone.py");
  const std::string kept = native(plain_ + "/kept.py"), folder = native(plain_ + "/legacy-folder");
  person_writes(kept, "k = 1");
  BLI_dir_create_recursive(folder.c_str());
  {
    std::ofstream out(record_, std::ios::binary);
    out << "F\t" << (here->first + 1) << "\t" << away << "\n"; /* Its volume is not here now. */
    out << "F\t" << here->first << "\t" << gone << "\n";      /* Gone from a volume that is here. */
    out << kept << "\n" << folder << "\n";                      /* The earlier format. */
  }
  changes_record_set(record_);
  EXPECT_TRUE(made(away));
  EXPECT_FALSE(made(gone));
  EXPECT_TRUE(made(kept));
  EXPECT_FALSE(made(folder + "/person.py")); /* A folder the earlier format recorded is no file. */
  const std::string text = record_text();
  EXPECT_NE(text.find(away), std::string::npos);
  EXPECT_EQ(text.find(gone), std::string::npos);
  EXPECT_NE(text.find("C\t"), std::string::npos); /* The kept file's identity and digest, taken now. */
}

TEST_F(ConfinementChangesTest, odd_names_survive_the_record)
{
#ifdef _WIN32
  /* Windows names hold no tab or line break: the escape itself (`%41` is never read back as `A`) still counts. */
  const std::string odd = plain_ + "/a%41b%25c.py", decoded = plain_ + "/aAb%c.py";
#else
  const std::string odd = plain_ + "/a\tb%41\nc.py", decoded = plain_ + "/a";
#endif
  script_writes(odd, "o = 1");
  changes_settle();
  changes_record_set(record_); /* A later session reads it back. */
  EXPECT_TRUE(made(odd));
  EXPECT_FALSE(made(decoded));
}

TEST_F(ConfinementChangesTest, forget_a_folder)
{
  const std::string project = plain_ + "/forget-me";
  script_makes_folder(project);
  script_writes(project + "/tool.py", "t = 'tool'");
  changes_settle();
  EXPECT_TRUE(made(project + "/tool.py"));
  EXPECT_GT(changes_forget(key(project)), 0);
  EXPECT_FALSE(made(project + "/tool.py"));
  person_writes(plain_ + "/copy.py", "t = 'tool'");
  EXPECT_TRUE(made(plain_ + "/copy.py")); /* A copy elsewhere is not forgotten: only that folder's files are. */
  changes_record_set(record_);
  EXPECT_FALSE(made(project + "/tool.py")); /* And stays forgotten. */
  EXPECT_TRUE(made(plain_ + "/copy.py"));
}

TEST_F(ConfinementChangesTest, a_later_write_through_a_kept_handle_is_recorded)
{
  const std::string kept = plain_ + "/kept.py";
  script_writes(kept, "k = 1");
  changes_settle(); /* The region that opened it ends: its content then is recorded. */
  person_writes(kept, "k = 'written later'"); /* Through the handle the script kept open: no event. */
  changes_settle(); /* A later step's region ends. */
  person_writes(plain_ + "/copy-of-kept.py", "k = 'written later'");
  EXPECT_TRUE(made(plain_ + "/copy-of-kept.py")); /* The content the script left is the script's wherever it goes. */
}

#ifndef _WIN32
TEST_F(ConfinementChangesTest, forget_follows_no_link)
{
  const std::string elsewhere = plain_ + "/elsewhere.py";
  script_writes(elsewhere, "e = 'elsewhere'");
  changes_settle();
  const std::string project = plain_ + "/forget-links";
  std::filesystem::create_directories(project);
  std::filesystem::create_symlink(elsewhere, project + "/link.py"); /* The person's folder holds only a link. */
  changes_forget(key(project));
  EXPECT_TRUE(made(elsewhere)); /* Forgetting a folder never reaches a file a link in it names. */
}
#endif

TEST_F(ConfinementChangesTest, fails_closed_and_recovers)
{
  /* A folder at the record's path: unreadable and unwritable whoever runs the test (root ignores modes). */
  script_writes(plain_ + "/a.py", "a = 1");
  changes_settle();
  const std::string aside = record_ + ".aside";
  ASSERT_EQ(std::rename(record_.c_str(), aside.c_str()), 0);
  ASSERT_TRUE(BLI_dir_create_recursive(record_.c_str()));
  person_writes(plain_ + "/person.py", "p = 1");
  person_writes(trusted_ + "/installed.py", "i = 1");
  EXPECT_EQ(folders_.judge(plain_ + "/b.py", true), Refusal::Private); /* Unrecordable: refused. */
  EXPECT_TRUE(judged_unrecorded);
  EXPECT_TRUE(made(plain_ + "/person.py")); /* Unreadable: only installed code loads. */
  EXPECT_FALSE(made(trusted_ + "/installed.py"));
  changes_record_set(record_); /* A later session that finds it so. */
  EXPECT_TRUE(made(plain_ + "/person.py"));
  BLI_delete(record_.c_str(), true, false);
  ASSERT_EQ(std::rename(aside.c_str(), record_.c_str()), 0);
  std::this_thread::sleep_for(std::chrono::milliseconds(FRESH_MS + 50)); /* A load looks again after this. */
  EXPECT_FALSE(made(plain_ + "/person.py")); /* Readable again: recovered. */
  EXPECT_TRUE(made(plain_ + "/a.py"));
}

}  // namespace blender::angelo_guard::tests
