/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Undo keeps or creates libraries for packed linked data, appended at the end of the library list,
 * and then gives them names no other library has (#blo_undo_libraries_names_ensure_unique).
 * Validating a name sorts the library into its place in the list: a walk over the list went on
 * from there and skipped the kept libraries after it, which could keep a duplicate name (a
 * critical corruption report on Undo). Here: two kept libraries at the end, in reverse name order,
 * the second named like a library the step read.
 */

#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"

#include "BLI_listbase.hh"
#include "BLI_listbase_iterator.hh"
#include "BLI_set.hh"
#include "BLI_string.hh"

#include "DNA_ID.h"

#include "intern/readfile.hh"

#include "testing/testing.h"

namespace blender::tests {

class ReadfileUndoLibraryNamesTest : public bke::BlenderGTestBase {
 protected:
  Main *bmain = nullptr;
  void SetUp() override
  {
    bmain = BKE_main_new();
  }
  void TearDown() override
  {
    BKE_main_free(bmain);
  }
  Library *library(const char *created_as, const char *name)
  {
    Library *lib = BKE_id_new<Library>(bmain, created_as);
    BLI_strncpy(lib->id.name + 2, name, sizeof(lib->id.name) - 2); /* Not validated, as left. */
    return lib;
  }
};

TEST_F(ReadfileUndoLibraryNamesTest, KeptLibrariesInReverseNameOrderAllGetUniqueNames)
{
  Library *step = library("a", "Lib.001");
  Library *step_other = library("b", "Lib.002");
  Library *first_kept = library("c", "Lib.002");
  Library *second_kept = library("d", "Lib.001");
  /* The list as the undo read leaves it: the step's libraries, then the kept ones appended. Both
   * kept ones take a name already used; renaming the first sorts it after the second. */
  for (Library *lib : {step, step_other, first_kept, second_kept}) {
    BLI_remlink(&bmain->libraries, lib);
    BLI_addtail(&bmain->libraries, lib);
  }
  ASSERT_EQ(bmain->libraries.last(), second_kept);

  blo_undo_libraries_names_ensure_unique(*bmain, Set<Library *>{first_kept, second_kept});

  Set<std::string> names;
  for (const Library &lib : bmain->libraries) {
    EXPECT_TRUE(names.add(lib.id.name + 2)) << "two libraries named " << lib.id.name + 2;
  }
  /* The libraries the step read keep their names. */
  EXPECT_STREQ(step->id.name + 2, "Lib.001");
  EXPECT_STREQ(step_other->id.name + 2, "Lib.002");
  EXPECT_EQ(names.size(), 4);
}

}  // namespace blender::tests
