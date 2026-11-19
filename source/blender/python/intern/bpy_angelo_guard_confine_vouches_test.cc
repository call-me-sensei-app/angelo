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
    BLI_dir_create_recursive((root_ + "/versions").c_str());
    forget_scope(Scope::Session);
    forget_scope(Scope::Call);
    forget_scope(Scope::Pick);
    forget_scope(Scope::Typed);
    forget_scope(Scope::Run);
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

/* The break-it review of 29b385a6645 (MUST-1): a program vouched to run (a CLI found through a link) never becomes
 * a file a script's Blender writer may write, and runs only while it is the very file, unchanged, it was when
 * vouched: a script that rewrote it in place (an image unpacked onto it keeps its inode and exec bit) never gets its
 * bytes run unconfined. */
TEST_F(ConfinementVouchesTest, a_run_only_vouch_never_widens_writing)
{
  const std::string program = make("versions/2.1.300", "#!/bin/sh\necho one\n");
  ASSERT_TRUE(add_program(program, facts_of(program), false));
  EXPECT_TRUE(program_named(program, facts_of(program)));
  EXPECT_FALSE(matches(program, facts_of(program))) << "never a write vouch";
  EXPECT_FALSE(identity_named(facts_of(program))) << "nor the identity a hard link would write";
  EXPECT_FALSE(file_named_in(native(root_ + "/versions"), false));
}

TEST_F(ConfinementVouchesTest, a_program_rewritten_in_place_or_replaced_never_runs)
{
  const std::string program = make("versions/2.1.300", "#!/bin/sh\necho one\n");
  ASSERT_TRUE(add_program(program, facts_of(program), false));
  std::ofstream(program, std::ios::trunc) << "#!/bin/sh\necho payload, longer\n"; /* Same inode: O_TRUNC. */
  EXPECT_FALSE(program_named(program, facts_of(program))) << "rewritten in place";
  ASSERT_TRUE(add_program(program, facts_of(program), false)); /* Found again before any script: the person's. */
  EXPECT_TRUE(program_named(program, facts_of(program)));
  BLI_delete(program.c_str(), false, false);
  make("versions/2.1.300", "#!/bin/sh\necho another file\n");
  EXPECT_FALSE(program_named(program, facts_of(program))) << "another file under the same name";
}

TEST_F(ConfinementVouchesTest, a_program_found_again_after_a_script_keeps_what_was_captured)
{
  const std::string program = make("versions/2.1.300", "#!/bin/sh\necho one\n");
  ASSERT_TRUE(add_program(program, facts_of(program), false));
  std::ofstream(program, std::ios::trunc) << "#!/bin/sh\necho changed after a script ran\n";
  EXPECT_FALSE(add_program(program, facts_of(program), true)) << "a change is never blessed once scripts ran";
  EXPECT_FALSE(program_named(program, facts_of(program)));
  const std::string fresh = make("versions/2.1.301", "#!/bin/sh\necho an update's new version\n");
  EXPECT_TRUE(add_program(fresh, facts_of(fresh), true)) << "a program first found after a script is captured then";
  EXPECT_TRUE(program_named(fresh, facts_of(fresh)));
}

TEST_F(ConfinementVouchesTest, a_session_vouch_runs_a_program_only_while_it_is_unchanged)
{
  const std::string program = make("tool", "#!/bin/sh\necho one\n");
  ASSERT_TRUE(add(program, facts_of(program), false, false, Scope::Session, ""));
  EXPECT_TRUE(program_named(program, facts_of(program)));
  std::ofstream(program, std::ios::trunc) << "#!/bin/sh\necho rewritten in place\n";
  EXPECT_TRUE(matches(program, facts_of(program))) << "its write vouch is as it was";
  EXPECT_FALSE(program_named(program, facts_of(program))) << "but it no longer runs";
}

/* The re-review of 4be92cfb84f (S-1): a recorded program's captured facts outlive every way to drop them. Narrowing
 * (`unvouch_path`, which anyone may call) and the oldest-first trim never drop a program, so a script that rewrote it
 * can't have it forgotten and then recorded afresh when Angelo finds it again at the next start. */
TEST_F(ConfinementVouchesTest, a_program_unvouched_then_found_again_after_a_script_stays_refused)
{
  const std::string program = make("versions/2.1.300", "#!/bin/sh\necho one\n");
  ASSERT_TRUE(add_program(program, facts_of(program), false));
  std::ofstream(program, std::ios::trunc) << "#!/bin/sh\necho changed by a script\n";
  forget(program, false, false); /* A script's unvouch_path: narrowing, never a program's record. */
  forget(program, false, true);
  EXPECT_FALSE(add_program(program, facts_of(program), true)) << "found again: still what was captured";
  EXPECT_FALSE(program_named(program, facts_of(program)));
  EXPECT_TRUE(program_changed(program, facts_of(program))) << "what the refusal says";
}

TEST_F(ConfinementVouchesTest, the_oldest_first_trim_never_drops_a_program)
{
  const std::string program = make("versions/2.1.300", "#!/bin/sh\necho one\n");
  ASSERT_TRUE(add_program(program, facts_of(program), false));
  for (int i = 0; i < 4200; i++) { /* Past KEPT: the oldest entries go, never the program. */
    ASSERT_TRUE(add(native(root_ + "/folder/file" + std::to_string(i)), Facts(), false, false, Scope::Call, ""));
  }
  EXPECT_LE(count(), size_t(4096));
  EXPECT_TRUE(program_named(program, facts_of(program))) << "still recorded";
  std::ofstream(program, std::ios::trunc) << "#!/bin/sh\necho changed by a script\n";
  EXPECT_FALSE(add_program(program, facts_of(program), true)) << "its captured facts kept";
}

TEST_F(ConfinementVouchesTest, programs_past_their_bound_are_refused_never_evicted)
{
  Facts facts;
  facts.exists = true;
  size_t recorded = 0;
  for (int i = 0; i < 300; i++) {
    recorded += add_program(native(root_ + "/versions/p" + std::to_string(i)), facts, false) ? 1 : 0;
  }
  EXPECT_EQ(recorded, size_t(256)) << "a program past the bound is not recorded: it does not run";
  EXPECT_TRUE(add_program(native(root_ + "/versions/p0"), facts, false)) << "one already recorded still is";
}

TEST_F(ConfinementVouchesTest, a_program_changed_on_disk_is_told_apart_from_one_never_recorded)
{
  const std::string program = make("versions/2.1.300", "#!/bin/sh\necho one\n");
  EXPECT_FALSE(program_changed(program, facts_of(program))) << "never recorded";
  ASSERT_TRUE(add_program(program, facts_of(program), false));
  EXPECT_FALSE(program_changed(program, facts_of(program))) << "unchanged";
  std::ofstream(program, std::ios::trunc) << "#!/bin/sh\necho an update in place, longer\n";
  EXPECT_TRUE(program_changed(program, facts_of(program)));
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
