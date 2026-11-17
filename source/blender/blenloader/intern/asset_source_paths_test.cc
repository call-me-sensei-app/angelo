/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include <string>

#include "BLO_asset_source_paths.hh"

#include "BLI_string.hh"
#include "DNA_space_enums.h"
#include "testing/testing.h"

namespace blender::tests {

/* The fixture's pairs are spelled with forward slashes on every platform. `*_NATIVE` is the
 * spelling the platform's own tools give (and the add-on passes: Python's `Path.resolve()`).
 * `*_OTHERS` are paths that differ from a fixture path only in separators (every one swapped, or
 * some of them): on Windows each names the same file, elsewhere each names another one, because a
 * backslash is a character of a file name in an absolute path there. `DRIVE` is a prefix that is
 * a drive letter on Windows and nothing special elsewhere. */
#ifdef WIN32
#  define SOURCE_ROOT "C:/asset-original/"
#  define PRIVATE_ROOT "C:/asset-private/"
#  define SOURCE_ROOT_NATIVE "C:\\asset-original\\"
#  define PRIVATE_ROOT_NATIVE "C:\\asset-private\\"
#  define NATIVE_SEP "\\"
#  define SOURCE_LIBRARY_OTHERS \
    "C:\\asset-original\\shared\\library.blend", "C:\\asset-original/shared\\library.blend", \
        "C:/asset-original\\shared/library.blend", "C:/asset-original/shared\\library.blend"
#  define PRIVATE_ROOT_FILE_OTHERS \
    "C:\\asset-private\\root.blend", "C:\\asset-private/root.blend", "C:/asset-private\\root.blend"
#  define PRIVATE_A_OTHERS "C:\\asset-private\\a.blend", "C:/asset-private\\a.blend"
#else
#  define SOURCE_ROOT "/asset-original/"
#  define PRIVATE_ROOT "/asset-private/"
#  define SOURCE_ROOT_NATIVE SOURCE_ROOT
#  define PRIVATE_ROOT_NATIVE PRIVATE_ROOT
#  define NATIVE_SEP "/"
#  define SOURCE_LIBRARY_OTHERS \
    "/asset-original/shared\\library.blend", "/asset-original\\shared/library.blend", \
        "/asset-original\\shared\\library.blend"
#  define PRIVATE_ROOT_FILE_OTHERS "/asset-private\\root.blend"
#  define PRIVATE_A_OTHERS "/asset-private\\a.blend"
#endif

class AssetSourcePathsTest : public testing::Test {
 protected:
  std::shared_ptr<const BLOAssetSourcePaths> paths;

  void SetUp() override
  {
    ASSERT_FALSE(BLO_asset_source_paths_current());
    const BLOAssetSourcePathPair pairs[] = {
        {SOURCE_ROOT "scene/root.blend", PRIVATE_ROOT "root.blend"},
        {SOURCE_ROOT "shared/library.blend", PRIVATE_ROOT "library.blend"},
        {SOURCE_ROOT "textures/extra.blend", PRIVATE_ROOT "extra.blend"},
    };
    paths = BLO_asset_source_paths_create(Span(pairs, ARRAY_SIZE(pairs)), nullptr);
    ASSERT_TRUE(paths);
  }

  void TearDown() override
  {
    if (paths) {
      BLO_asset_source_paths_deactivate(*paths);
    }
    EXPECT_FALSE(BLO_asset_source_paths_current());
  }

  bool resolve(StringRef path, StringRef parent, char *result)
  {
    return BLO_asset_source_path_resolve(paths, path, parent, result);
  }
};

TEST_F(AssetSourcePathsTest, OriginalAbsoluteMapsBeforeAnyFileOpen)
{
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  char result[FILE_MAX];
  ASSERT_TRUE(resolve(SOURCE_ROOT "shared/library.blend", {}, result));
  EXPECT_STREQ(result, PRIVATE_ROOT "library.blend");
}

TEST_F(AssetSourcePathsTest, PrivateAbsoluteIsIdempotent)
{
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  char result[FILE_MAX];
  ASSERT_TRUE(resolve(PRIVATE_ROOT "root.blend", {}, result));
  EXPECT_STREQ(result, PRIVATE_ROOT "root.blend");
}

TEST_F(AssetSourcePathsTest, RelativeLibraryUsesOriginalParentNotCopiedLayout)
{
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  char result[FILE_MAX];
  ASSERT_TRUE(resolve("//../shared/library.blend", PRIVATE_ROOT "root.blend", result));
  EXPECT_STREQ(result, PRIVATE_ROOT "library.blend");
  ASSERT_TRUE(resolve("//../textures/extra.blend", result, result));
  EXPECT_STREQ(result, PRIVATE_ROOT "extra.blend");
}

TEST_F(AssetSourcePathsTest, UnmappedReferenceNeverFallsBackToOriginal)
{
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  char result[FILE_MAX] = "unchanged";
  EXPECT_FALSE(resolve(SOURCE_ROOT "not-selected.blend", {}, result));
  EXPECT_STREQ(result, "unchanged");
  EXPECT_FALSE(resolve("//not-selected.blend", PRIVATE_ROOT "root.blend", result));
  EXPECT_STREQ(result, "unchanged");
}

TEST_F(AssetSourcePathsTest, RelativeRequiresExactMappedPrivateParent)
{
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  char result[FILE_MAX];
  EXPECT_FALSE(resolve("//../shared/library.blend", SOURCE_ROOT "scene/root.blend", result));
  EXPECT_FALSE(resolve("//../shared/library.blend", {}, result));
  EXPECT_FALSE(resolve("library.blend", PRIVATE_ROOT "root.blend", result));
}

TEST_F(AssetSourcePathsTest, RetainedReaderCannotUseRouteAfterScopeEndsOrUnderNewScope)
{
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  BLO_asset_source_paths_deactivate(*paths);
  char result[FILE_MAX];
  EXPECT_FALSE(resolve(PRIVATE_ROOT "root.blend", {}, result));
  const BLOAssetSourcePathPair pair = {SOURCE_ROOT "scene/root.blend", PRIVATE_ROOT "root.blend"};
  auto replacement = BLO_asset_source_paths_create(Span(&pair, 1), nullptr);
  ASSERT_TRUE(replacement);
  ASSERT_TRUE(BLO_asset_source_paths_activate(replacement, nullptr));
  EXPECT_FALSE(resolve(PRIVATE_ROOT "root.blend", {}, result));
  BLO_asset_source_paths_deactivate(*paths);  // A stale owner cannot deactivate its successor.
  EXPECT_EQ(BLO_asset_source_paths_current(), replacement);
  BLO_asset_source_paths_deactivate(*replacement);
}

TEST_F(AssetSourcePathsTest, NestedActivationRefusesAndPreservesOriginal)
{
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  const char *error = nullptr;
  EXPECT_FALSE(BLO_asset_source_paths_activate(paths, &error));
  EXPECT_NE(error, nullptr);
  EXPECT_EQ(BLO_asset_source_paths_current(), paths);
}

TEST_F(AssetSourcePathsTest, EmptyActiveRouteSupportsNoBlendUseButRefusesAnyBlendOpen)
{
  auto empty = BLO_asset_source_paths_create({}, nullptr);
  ASSERT_TRUE(empty);
  ASSERT_TRUE(BLO_asset_source_paths_activate(empty, nullptr));
  char result[FILE_MAX];
  EXPECT_FALSE(BLO_asset_source_path_resolve(empty, PRIVATE_ROOT "root.blend", {}, result));
  BLO_asset_source_paths_deactivate(*empty);
}

TEST_F(AssetSourcePathsTest, RejectsAmbiguousCrossAliasAndDuplicateMappings)
{
  const BLOAssetSourcePathPair duplicate[] = {
      {SOURCE_ROOT "a.blend", PRIVATE_ROOT "a.blend"},
      {SOURCE_ROOT "a.blend", PRIVATE_ROOT "b.blend"},
  };
  EXPECT_FALSE(BLO_asset_source_paths_create(Span(duplicate, ARRAY_SIZE(duplicate)), nullptr));
  const BLOAssetSourcePathPair cross_alias[] = {
      {SOURCE_ROOT "a.blend", PRIVATE_ROOT "a.blend"},
      {PRIVATE_ROOT "a.blend", PRIVATE_ROOT "b.blend"},
  };
  EXPECT_FALSE(BLO_asset_source_paths_create(Span(cross_alias, ARRAY_SIZE(cross_alias)), nullptr));
}

TEST_F(AssetSourcePathsTest, RejectsRelativeNoncanonicalEmbeddedNullAndSameCopy)
{
  const BLOAssetSourcePathPair relative = {"//a.blend", PRIVATE_ROOT "a.blend"};
  EXPECT_FALSE(BLO_asset_source_paths_create(Span(&relative, 1), nullptr));
  const BLOAssetSourcePathPair noncanonical = {SOURCE_ROOT "../a.blend", PRIVATE_ROOT "a.blend"};
  EXPECT_FALSE(BLO_asset_source_paths_create(Span(&noncanonical, 1), nullptr));
  const char null_path[] = SOURCE_ROOT "a.blend\0suffix";
  const BLOAssetSourcePathPair embedded = {StringRef(null_path, sizeof(null_path) - 1),
                                          PRIVATE_ROOT "a.blend"};
  EXPECT_FALSE(BLO_asset_source_paths_create(Span(&embedded, 1), nullptr));
  const BLOAssetSourcePathPair same = {SOURCE_ROOT "a.blend", SOURCE_ROOT "a.blend"};
  EXPECT_FALSE(BLO_asset_source_paths_create(Span(&same, 1), nullptr));
}

/* What the add-on passes on each platform: the platform's own spelling, already canonical. A
 * library reference inside a file is relative in the spelling of the platform that saved it.
 * This describes what has always worked (#BLI_path_abs reads a relative reference with either
 * separator on every platform) and keeps it so; it does not depend on the one spelling. */
TEST_F(AssetSourcePathsTest, NativePairsResolveReferencesWrittenOnEitherPlatform)
{
  const BLOAssetSourcePathPair pairs[] = {
      {SOURCE_ROOT_NATIVE "scene" NATIVE_SEP "root.blend", PRIVATE_ROOT_NATIVE "root.blend"},
      {SOURCE_ROOT_NATIVE "shared" NATIVE_SEP "library.blend",
       PRIVATE_ROOT_NATIVE "library.blend"},
  };
  paths = BLO_asset_source_paths_create(Span(pairs, ARRAY_SIZE(pairs)), nullptr);
  ASSERT_TRUE(paths);
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  char result[FILE_MAX];
  ASSERT_TRUE(resolve("//../shared/library.blend", PRIVATE_ROOT_NATIVE "root.blend", result));
  EXPECT_STREQ(result, PRIVATE_ROOT_NATIVE "library.blend");
  ASSERT_TRUE(resolve("//..\\shared\\library.blend", PRIVATE_ROOT_NATIVE "root.blend", result));
  EXPECT_STREQ(result, PRIVATE_ROOT_NATIVE "library.blend");
  ASSERT_TRUE(resolve(SOURCE_ROOT_NATIVE "shared" NATIVE_SEP "library.blend", {}, result));
  EXPECT_STREQ(result, PRIVATE_ROOT_NATIVE "library.blend");
  const BLOAssetSourcePathPair noncanonical = {SOURCE_ROOT_NATIVE ".." NATIVE_SEP "a.blend",
                                               PRIVATE_ROOT_NATIVE "a.blend"};
  EXPECT_FALSE(BLO_asset_source_paths_create(Span(&noncanonical, 1), nullptr));
}

/* A relative reference is joined under its parent's folder: separators after `//` do not start
 * again at a root, with either separator, on every platform. The add-on reads a reference the
 * same way (`blender_assets.dependency_path`). Describes #BLI_path_abs as it has always been. */
TEST_F(AssetSourcePathsTest, RelativeReferenceStaysUnderItsParentFolder)
{
  const BLOAssetSourcePathPair pairs[] = {
      {SOURCE_ROOT "scene/root.blend", PRIVATE_ROOT "root.blend"},
      {SOURCE_ROOT "scene/nested/library.blend", PRIVATE_ROOT "nested.blend"},
      {SOURCE_ROOT "nested/library.blend", PRIVATE_ROOT "outside.blend"},
  };
  paths = BLO_asset_source_paths_create(Span(pairs, ARRAY_SIZE(pairs)), nullptr);
  ASSERT_TRUE(paths);
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  for (const char *reference : {"//nested/library.blend",
                                "///nested/library.blend",
                                "//\\nested\\library.blend",
                                "//\\/nested/library.blend"})
  {
    SCOPED_TRACE(reference);
    char result[FILE_MAX] = "unchanged";
    ASSERT_TRUE(resolve(reference, PRIVATE_ROOT "root.blend", result));
    EXPECT_STREQ(result, PRIVATE_ROOT "nested.blend");
  }
}

/* On Windows either separator names the same file, whoever asks and however a pair or a lookup
 * mixes them. Elsewhere a backslash in an absolute path is a character of a file name: the same
 * text names another file. Every lookup's own answer is checked. */
TEST_F(AssetSourcePathsTest, TheOtherSeparatorNamesTheSameFileOnWindowsOnly)
{
#ifdef WIN32
  const bool same_file = true;
#else
  const bool same_file = false;
#endif
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  for (const char *spelling : {SOURCE_LIBRARY_OTHERS}) {
    SCOPED_TRACE(spelling);
    char result[FILE_MAX] = "unchanged";
    EXPECT_EQ(resolve(spelling, {}, result), same_file);
    /* The private path as it was registered, never as it was asked for. */
    EXPECT_STREQ(result, same_file ? PRIVATE_ROOT "library.blend" : "unchanged");
    const BLOAssetSourcePathPair twice[] = {
        {SOURCE_ROOT "shared/library.blend", PRIVATE_ROOT "a.blend"},
        {spelling, PRIVATE_ROOT "b.blend"},
    };
    /* One original under two spellings, or two different files. */
    EXPECT_EQ(bool(BLO_asset_source_paths_create(Span(twice, ARRAY_SIZE(twice)), nullptr)),
              !same_file);
  }
  for (const char *parent : {PRIVATE_ROOT_FILE_OTHERS}) {
    SCOPED_TRACE(parent);
    char result[FILE_MAX] = "unchanged";
    EXPECT_EQ(resolve("//../shared/library.blend", parent, result), same_file);
    EXPECT_STREQ(result, same_file ? PRIVATE_ROOT "library.blend" : "unchanged");
  }
  /* An original that is an earlier pair's private copy in another spelling. */
  for (const char *alias : {PRIVATE_A_OTHERS}) {
    SCOPED_TRACE(alias);
    const BLOAssetSourcePathPair cross_alias[] = {
        {SOURCE_ROOT "a.blend", PRIVATE_ROOT "a.blend"},
        {alias, PRIVATE_ROOT "b.blend"},
    };
    EXPECT_EQ(
        bool(BLO_asset_source_paths_create(Span(cross_alias, ARRAY_SIZE(cross_alias)), nullptr)),
        !same_file);
    const BLOAssetSourcePathPair same_copy = {PRIVATE_ROOT "a.blend", alias};
    EXPECT_EQ(bool(BLO_asset_source_paths_create(Span(&same_copy, 1), nullptr)), !same_file);
  }
}

/* A path is canonical when no shorter spelling names the same place, whichever separator the
 * platform reads spells the detour. */
TEST_F(AssetSourcePathsTest, RejectsEveryNoncanonicalSpelling)
{
  for (const char *noncanonical :
       {
           SOURCE_ROOT "./a.blend",
           SOURCE_ROOT "/a.blend",
           SOURCE_ROOT "sub/../a.blend",
           SOURCE_ROOT "sub/./a.blend",
           SOURCE_ROOT "sub//a.blend",
           SOURCE_ROOT_NATIVE "." NATIVE_SEP "a.blend",
           SOURCE_ROOT_NATIVE NATIVE_SEP "a.blend",
#ifdef WIN32
           "C://asset-original/a.blend",
           "C:\\asset-original/.\\a.blend",
           "C:/asset-original\\../a.blend",
           "C:\\asset-original/\\a.blend",
#endif
       })
  {
    SCOPED_TRACE(noncanonical);
    const BLOAssetSourcePathPair as_original = {noncanonical, PRIVATE_ROOT "a.blend"};
    EXPECT_FALSE(BLO_asset_source_paths_create(Span(&as_original, 1), nullptr));
    const BLOAssetSourcePathPair as_private = {SOURCE_ROOT "a.blend", noncanonical};
    EXPECT_FALSE(BLO_asset_source_paths_create(Span(&as_private, 1), nullptr));
  }
}

/* `C:name` and a bare `C:` mean a place under the drive's current folder on Windows, so they are
 * not absolute; elsewhere they do not start at the root. Neither may be registered or asked for,
 * with either separator after the first name. */
TEST_F(AssetSourcePathsTest, PathRelativeToADriveIsNotAbsolute)
{
  for (const char *drive_relative :
       {
           "C:",
           "C:a.blend",
           "C:shared/library.blend",
           "C:shared\\library.blend",
           "C:../a.blend",
           "C:..\\a.blend",
       })
  {
    SCOPED_TRACE(drive_relative);
    const BLOAssetSourcePathPair as_original = {drive_relative, PRIVATE_ROOT "a.blend"};
    EXPECT_FALSE(BLO_asset_source_paths_create(Span(&as_original, 1), nullptr));
    const BLOAssetSourcePathPair as_private = {SOURCE_ROOT "a.blend", drive_relative};
    EXPECT_FALSE(BLO_asset_source_paths_create(Span(&as_private, 1), nullptr));
  }
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  char result[FILE_MAX] = "unchanged";
  EXPECT_FALSE(resolve("C:shared/library.blend", {}, result));
  EXPECT_FALSE(resolve("//../shared/library.blend", "C:root.blend", result));
  EXPECT_STREQ(result, "unchanged");
}

/* A reference too long to join to its parent's folder names nothing. Cut short at the buffer's
 * end it would lose the end of its last name and could become a registered path. */
TEST_F(AssetSourcePathsTest, ReferenceTooLongToJoinIsRefused)
{
  ASSERT_TRUE(BLO_asset_source_paths_activate(paths, nullptr));
  const std::string folder = SOURCE_ROOT "scene/";
  const std::string registered = "../shared/library.blend";
  /* Detours that vanish when the path is normalized, sized so that the registered name ends
   * exactly where a FILE_MAX buffer does. */
  const size_t detour_len = FILE_MAX - 1 - folder.size() - registered.size();
  std::string detours;
  while (detours.size() + 2 <= detour_len) {
    detours += "./";
  }
  if (detours.size() < detour_len) {
    detours += "/";
  }
  char result[FILE_MAX] = "unchanged";
  /* The longest reference that still fits is the registered file. */
  ASSERT_TRUE(resolve("//" + detours + registered, PRIVATE_ROOT "root.blend", result));
  EXPECT_STREQ(result, PRIVATE_ROOT "library.blend");
  STRNCPY(result, "unchanged");
  /* One more character is another name, not that file. */
  for (const char *suffix : {"x", ".bak", "/more.blend"}) {
    SCOPED_TRACE(suffix);
    EXPECT_FALSE(resolve("//" + detours + registered + suffix, PRIVATE_ROOT "root.blend", result));
    EXPECT_STREQ(result, "unchanged");
  }
}

TEST_F(AssetSourcePathsTest, NullRouteIsNotAssetAuthority)
{
  char result[FILE_MAX];
  EXPECT_FALSE(BLO_asset_source_path_resolve(nullptr, PRIVATE_ROOT "root.blend", {}, result));
}

}  // namespace blender::tests
