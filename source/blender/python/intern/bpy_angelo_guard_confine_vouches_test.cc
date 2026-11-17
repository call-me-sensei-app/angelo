/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The record of paths the person named for Angelo's own writers (bpy_angelo_guard_confine_vouches.cc), against the
 * piece-10 review's rulings: a named file is that very file (a replacement or a hard link put at its name is not), a
 * folder named for a pick or a typed path covers what lies directly in it unless named whole, a property's typed
 * value is forgotten when the property changes, and a pick ends with its operator's run.
 */

#include "testing/testing.h"

#include <cstdlib>
#include <fstream>
#include <string>

#ifndef _WIN32
#  include <unistd.h>
#endif

#include "BLI_fileops.hh"
#include "BLI_tempfile.hh"

#include "bpy_angelo_guard_confine_vouches.hh"

namespace blender::angelo_guard::vouches::tests {

class ConfinementVouchesTest : public testing::Test {
 protected:
  std::string root_;

  void SetUp() override
  {
    char temp[1024];
    BLI_temp_directory_path_get(temp, sizeof(temp));
    std::string base(temp);
    if (!base.empty() && base.back() != '/' && base.back() != '\\') {
      base += '/';
    }
    root_ = native(base + "angelo-vouches-" + std::to_string(std::rand()));
    BLI_dir_create_recursive((root_ + "/folder/inner").c_str());
    forget_scope(Scope::Session);
    forget_scope(Scope::Call);
    forget_scope(Scope::Pick);
    forget_scope(Scope::Typed);
  }

  void TearDown() override
  {
    BLI_delete(root_.c_str(), true, true);
  }

  /** A key as C spells one (writable.cc's `key_of`): the platform's own separator. The vouches compare keys as
   * given, so a key joined with `/` would never lie inside a Windows folder's key. */
  static std::string native(std::string path)
  {
#ifdef _WIN32
    for (char &c : path) {
      c = c == '/' ? '\\' : c;
    }
#endif
    return path;
  }

  std::string make(const std::string &name, const char *text)
  {
    const std::string path = native(root_ + "/" + name);
    std::ofstream(path) << text;
    return path;
  }
};

TEST_F(ConfinementVouchesTest, a_named_file_is_its_name_and_its_identity_is_kept)
{
  const std::string file = make("folder/thesis.txt", "the person's");
  ASSERT_TRUE(add(file, facts_of(file), false, false, Scope::Session, ""));
  EXPECT_TRUE(matches(file, facts_of(file)));
  EXPECT_TRUE(identity_named(facts_of(file)));
  EXPECT_FALSE(matches(native(root_ + "/folder/other.txt"), Facts())) << "only that name";
  /* Rewritten (a temporary renamed onto it): still the name named; the new file is not the identity named. */
  BLI_delete(file.c_str(), false, false);
  make("folder/thesis.txt", "rewritten");
  EXPECT_TRUE(matches(file, facts_of(file)));
}

#ifndef _WIN32
TEST_F(ConfinementVouchesTest, a_hard_link_put_at_a_named_name_is_another_file)
{
  const std::string person = make("person.txt", "the person's");
  const std::string named = native(root_ + "/folder/report.txt");
  ASSERT_TRUE(add(named, facts_of(named), false, false, Scope::Session, "")); /* Named before it existed. */
  ASSERT_EQ(link(person.c_str(), named.c_str()), 0);
  const Facts now = facts_of(named);
  EXPECT_EQ(now.links, 2u);
  EXPECT_FALSE(identity_named(now)) << "a file of the person's, linked in: never the one named";
}
#endif

/* The re-read of f9f24cc6ffd (round 3): a typed output pattern covers what Blender expands it to for that writer,
 * and nothing a script could choose: no other folder, no other name, no other property. */
TEST_F(ConfinementVouchesTest, a_typed_pattern_covers_its_own_expansions_only)
{
  const std::string pattern = native(root_ + "/folder/{camera_name}/shot_####");
  ASSERT_TRUE(add(pattern, Facts(), false, false, Scope::Typed, "id7:0x1/filepath"));
  EXPECT_TRUE(matches_typed(native(root_ + "/folder/cam a/shot_0002.png"), "id7:0x1/filepath"));
  EXPECT_TRUE(matches_typed(native(root_ + "/folder/cam a/shot_12345.exr"), "id7:0x1/filepath"));
  EXPECT_FALSE(matches_typed(native(root_ + "/folder/cam a/shot_final.docx"), "id7:0x1/filepath"));
  EXPECT_FALSE(matches_typed(native(root_ + "/folder/a/b/shot_0002.png"), "id7:0x1/filepath")) << "one name";
  EXPECT_FALSE(matches_typed(native(root_ + "/elsewhere/cam/shot_0002.png"), "id7:0x1/filepath"));
  EXPECT_FALSE(matches_typed(native(root_ + "/folder/cam a/shot_0002.png"), "id7:0x2/filepath"))
      << "another writer's property";
  const std::string prefix = native(root_ + "/folder/out_");
  ASSERT_TRUE(add(prefix, Facts(), false, false, Scope::Typed, "id8:0x1/filepath"));
  EXPECT_TRUE(matches_typed(native(root_ + "/folder/out_0001.png"), "id8:0x1/filepath")) << "frames appended";
  EXPECT_FALSE(matches_typed(native(root_ + "/folder/out_thesis.docx"), "id8:0x1/filepath"));
}

TEST_F(ConfinementVouchesTest, a_typed_folder_covers_only_what_lies_directly_in_it_for_its_own_writer)
{
  const std::string folder = native(root_ + "/folder");
  ASSERT_TRUE(add(folder, facts_of(folder), true, false, Scope::Typed, "render/filepath"));
  EXPECT_TRUE(matches_typed(native(folder + "/frame_0001.png"), "render/filepath"));
  EXPECT_FALSE(matches_typed(native(folder + "/inner/deeper.png"), "render/filepath"));
  EXPECT_FALSE(matches_typed(native(root_ + "/elsewhere.png"), "render/filepath"));
  /* Tied to the writer that reads that property (the re-review of piece 10, SHOULD 2): never another writer. */
  EXPECT_FALSE(matches(native(folder + "/frame_0001.png"), Facts()));
  EXPECT_FALSE(matches_typed(native(folder + "/frame_0001.png"), "node/base_path"));
  ASSERT_TRUE(add(folder, facts_of(folder), true, true, Scope::Call, ""));
  EXPECT_TRUE(matches(native(folder + "/inner/deeper.png"), Facts())) << "one call's folder of Blender's own: whole";
}

TEST_F(ConfinementVouchesTest, a_typed_value_goes_when_the_property_changes)
{
  const std::string first = native(root_ + "/folder/out.png"), second = native(root_ + "/folder/other.png");
  ASSERT_TRUE(add(first, Facts(), false, false, Scope::Typed, "0x1/filepath"));
  EXPECT_TRUE(matches_typed(first, "0x1/filepath"));
  ASSERT_TRUE(add(second, Facts(), false, false, Scope::Typed, "0x1/filepath"));
  EXPECT_FALSE(matches_typed(first, "0x1/filepath")) << "the property holds one value: the earlier one is no target";
  EXPECT_TRUE(matches_typed(second, "0x1/filepath"));
  forget_tag("0x1/filepath");
  EXPECT_FALSE(matches_typed(second, "0x1/filepath")) << "set by anything but the person: it no longer counts";
}

TEST_F(ConfinementVouchesTest, a_pick_ends_with_its_operators_run)
{
  const std::string picked = native(root_ + "/folder/export.glb");
  ASSERT_TRUE(add(picked, Facts(), false, false, Scope::Pick, ""));
  EXPECT_TRUE(matches(picked, Facts()));
  forget_scope(Scope::Pick);
  EXPECT_FALSE(matches(picked, Facts()));
}

TEST_F(ConfinementVouchesTest, a_pick_counts_only_for_an_unconfined_caller)
{
  /* The re-read of 70600d6438b, MUST-1: a handler a script left, running inside the person's operator, can't write
   * over the file the person picked for that operator (writable.cc's `picks_count`). */
  const std::string picked = native(root_ + "/folder/export.glb");
  ASSERT_TRUE(add(picked, Facts(), false, false, Scope::Pick, ""));
  EXPECT_TRUE(matches(picked, Facts(), true));
  EXPECT_FALSE(matches(picked, Facts(), false)) << "a confined caller never writes over a pick";
  forget_scope(Scope::Pick);
}

TEST_F(ConfinementVouchesTest, a_link_is_never_named)
{
#ifndef _WIN32
  const std::string target = make("person.txt", "x");
  const std::string at = native(root_ + "/folder/link.txt");
  ASSERT_EQ(symlink(target.c_str(), at.c_str()), 0);
  EXPECT_FALSE(add(at, facts_of(at), false, false, Scope::Session, ""));
#endif
  EXPECT_FALSE(add("", Facts(), false, false, Scope::Session, ""));
}

}  // namespace blender::angelo_guard::vouches::tests
