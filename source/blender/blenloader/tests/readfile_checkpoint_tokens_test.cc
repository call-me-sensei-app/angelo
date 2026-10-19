/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include <cstdio>
#include <iterator>
#include <memory>
#include "BLI_fileops.h"
#include "BLI_listbase.h"
#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "BKE_key.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_mesh.h"
#include "BKE_object.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"
#include "DNA_key_types.h"
#include "DNA_layer_types.h"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "BLO_readfile.hh"
#include "BLO_writefile.hh"
#include "intern/checkpoint_write_context.hh"
#include "blendfile_loading_base_test.h"
#ifdef WIN32
#  include <io.h>
#else
#  include <unistd.h>
#endif
namespace blender {
namespace {
class ReadfileCheckpointTokensTest : public BlendfileLoadingBaseTest {
 protected:
  Main *main_ = nullptr;
  Scene *scene_ = nullptr;
  Object *object_ = nullptr;
  Mesh *mesh_ = nullptr;
  Key *key_ = nullptr;
  FILE *file_ = nullptr;
  ReportList reports_{};
  uint64_t object_token_ = 0, mesh_token_ = 0, key_token_ = 0, scene_token_ = 0, layer_token_ = 0;
  void SetUp() override
  {
    BKE_reports_init(&reports_, RPT_STORE | RPT_PRINT_HANDLED_BY_OWNER);
    main_ = BKE_main_new();
    scene_ = BKE_scene_add(main_, "SelectedScene");
    mesh_ = BKE_mesh_add(main_, "SelectedMesh");
    object_ = BKE_object_add_only_object(main_, OB_MESH, "SelectedObject");
    /* add_only_object starts unused. The fixture retains one ordinary counted owner;
     * tests of explicit unused inclusion reset this count to zero themselves. */
    id_us_plus(&object_->id);
    object_->data = &mesh_->id;
    key_ = BKE_key_add(main_, &mesh_->id);
    mesh_->key = key_;
    BKE_keyblock_add(key_, "Basis");
    file_ = tmpfile();
    ASSERT_NE(file_, nullptr);
  }
  void TearDown() override
  {
    if (file_) { EXPECT_EQ(fclose(file_), 0); }
    if (main_) { BKE_main_free(main_); }
    BKE_reports_free(&reports_);
    BlendfileLoadingBaseTest::TearDown();
  }
  int descriptor() const
  {
#ifdef WIN32
    return _fileno(file_);
#else
    return fileno(file_);
#endif
  }
  bool write()
  {
    CheckpointWriteContext context;
    context.user_data = this;
    context.before_local_ids = [](Main &, Span<ID *>, const CheckpointWriteTokenLookup &tokens,
                                   ReportList *, void *user_data) {
      auto &test = *static_cast<ReadfileCheckpointTokensTest *>(user_data);
      auto object = tokens.id_token(test.object_->id), mesh = tokens.id_token(test.mesh_->id);
      auto key = tokens.id_token(test.key_->id), scene = tokens.id_token(test.scene_->id);
      if (!object || !mesh || !key || !scene) { return false; }
      test.object_token_ = *object; test.mesh_token_ = *mesh;
      test.key_token_ = *key; test.scene_token_ = *scene;
      return true;
    };
    context.after_local_ids = [](Main &, Span<ID *>, const CheckpointWriteTokenLookup &tokens,
                                  ReportList *, void *user_data) {
      auto &test = *static_cast<ReadfileCheckpointTokensTest *>(user_data);
      const auto token = tokens.view_layer_token(
          *test.scene_, *static_cast<const ViewLayer *>(test.scene_->view_layers.first));
      if (!token) { return false; }
      test.layer_token_ = *token;
      return true;
    };
    BlendFileWriteParams params{};
    params.use_save_as_copy = true;
#ifdef WIN32
    const char *logical_path = "C:\\checkpoint-native-reader-fixture.blend";
#else
    const char *logical_path = "/tmp/checkpoint-native-reader-fixture.blend";
#endif
    return BLO_write_checkpoint_to_descriptor(main_, logical_path, descriptor(), 0, &params, &reports_, context);
  }
  BlendFileData *read(BLOCheckpointReadTokens &tokens)
  {
    BlendFileReadReport reports{};
    reports.reports = &reports_;
#ifdef WIN32
    const char *logical_path = "C:\\checkpoint-native-reader-fixture.blend";
#else
    const char *logical_path = "/tmp/checkpoint-native-reader-fixture.blend";
#endif
    return BLO_read_checkpoint_from_descriptor(logical_path, descriptor(), &reports, tokens);
  }
};
TEST_F(ReadfileCheckpointTokensTest, ResolvesActualTypedIdsAndViewLayerBeforeMapDisposal)
{
  ASSERT_TRUE(write());
  BLOCheckpointIDToken ids[] = {{object_token_, ID_OB}, {mesh_token_, ID_ME}, {key_token_, ID_KE}, {scene_token_, ID_SCE}};
  BLOCheckpointLayerToken layers[] = {{scene_token_, layer_token_}};
  BLOCheckpointReadTokens tokens{
      MutableSpan<BLOCheckpointIDToken>(ids, std::size(ids)),
      MutableSpan<BLOCheckpointLayerToken>(layers, std::size(layers))};
  std::unique_ptr<BlendFileData, decltype(&BLO_blendfiledata_free)> read_data(read(tokens), BLO_blendfiledata_free);
  ASSERT_NE(read_data, nullptr);
  ASSERT_TRUE(tokens.complete);
  auto *object = id_cast<Object *>(ids[0].resolved);
  auto *mesh = id_cast<Mesh *>(ids[1].resolved);
  EXPECT_NE(object, object_);
  EXPECT_EQ(object->data, &mesh->id);
  EXPECT_EQ(mesh->key, id_cast<Key *>(ids[2].resolved));
  EXPECT_EQ(layers[0].scene, id_cast<Scene *>(ids[3].resolved));
  EXPECT_GE(BLI_findindex(&layers[0].scene->view_layers, layers[0].layer), 0);
  BLI_stat_t info;
  EXPECT_EQ(BLI_fstat(descriptor(), &info), 0); /* Borrowed descriptor remains open. */
}
TEST_F(ReadfileCheckpointTokensTest, ForeignViewLayerTokenRefusesDetachedMain)
{
  ASSERT_TRUE(write());
  BLOCheckpointIDToken ids[] = {{object_token_, ID_OB}, {scene_token_, ID_SCE}};
  BLOCheckpointLayerToken layers[] = {{scene_token_, UINT64_MAX}};
  BLOCheckpointReadTokens tokens{
      MutableSpan<BLOCheckpointIDToken>(ids, std::size(ids)),
      MutableSpan<BLOCheckpointLayerToken>(layers, std::size(layers))};
  EXPECT_EQ(read(tokens), nullptr);
  EXPECT_FALSE(tokens.complete);
  BLI_stat_t info;
  EXPECT_EQ(BLI_fstat(descriptor(), &info), 0);
}
TEST_F(ReadfileCheckpointTokensTest, WrongIdTypeCannotBecomeObjectAuthority)
{
  ASSERT_TRUE(write());
  BLOCheckpointIDToken ids[] = {{scene_token_, ID_OB}};
  BLOCheckpointReadTokens tokens{MutableSpan<BLOCheckpointIDToken>(ids, std::size(ids)), {}};
  EXPECT_EQ(read(tokens), nullptr);
  EXPECT_FALSE(tokens.complete);
}
TEST_F(ReadfileCheckpointTokensTest, IncompleteBaseNeverClosesBorrowedDescriptor)
{
  ASSERT_EQ(fwrite("BLEND", 1, 5, file_), 5);
  ASSERT_EQ(fflush(file_), 0);
  BLOCheckpointIDToken ids[] = {{16, ID_OB}};
  BLOCheckpointReadTokens tokens{MutableSpan<BLOCheckpointIDToken>(ids, std::size(ids)), {}};
  EXPECT_EQ(read(tokens), nullptr);
  EXPECT_FALSE(tokens.complete);
  BLI_stat_t info;
  EXPECT_EQ(BLI_fstat(descriptor(), &info), 0);
}
}  // namespace
}  // namespace blender
