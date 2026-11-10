/* SPDX-FileCopyrightText: 2026 Angelo Contributors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "BLO_asset_source_paths.hh"

#include "DNA_space_enums.h"
#include "testing/testing.h"

namespace blender::tests {

#ifdef WIN32
#  define SOURCE_ROOT "C:/asset-original/"
#  define PRIVATE_ROOT "C:/asset-private/"
#else
#  define SOURCE_ROOT "/asset-original/"
#  define PRIVATE_ROOT "/asset-private/"
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

TEST_F(AssetSourcePathsTest, NullRouteIsNotAssetAuthority)
{
  char result[FILE_MAX];
  EXPECT_FALSE(BLO_asset_source_path_resolve(nullptr, PRIVATE_ROOT "root.blend", {}, result));
}

}  // namespace blender::tests
