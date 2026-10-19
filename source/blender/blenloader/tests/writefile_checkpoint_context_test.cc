/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <chrono>
#include <cstring>
#include <filesystem>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include <fcntl.h>
#ifdef WIN32
#  include <io.h>
#else
#  include <unistd.h>
#endif

#include "DNA_fileglobal_types.h"
#include "DNA_key_types.h"
#include "DNA_material_types.h"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "DNA_screen_types.h"
#include "DNA_sdna_type_ids.hh"
#include "DNA_windowmanager_types.h"
#include "DNA_workspace_types.h"

#include "BLI_fileops.h"
#include "BLI_filereader.h"
#include "BLI_path_utils.hh"
#include "BLI_string.h"
#include "BLI_tempfile.h"

#include "BKE_blender.hh"
#include "BKE_global.hh"
#include "BKE_key.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_override.hh"
#include "BKE_main.hh"
#include "BKE_mesh.h"
#include "BKE_object.hh"
#include "BKE_object_types.hh"
#include "BKE_paint.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"
#include "BKE_wm_runtime.hh"
#include "BKE_workspace.hh"

#include "MEM_guardedalloc.h"

#include "wm_window.hh"

#include "BLO_core_bhead.hh"
#include "BLO_core_blend_header.hh"
#include "BLO_writefile.hh"

#include "blendfile_loading_base_test.h"
#include "intern/checkpoint_write_context.hh"

namespace blender {
namespace {

class WritefileCheckpointContextTest : public BlendfileLoadingBaseTest {
 protected:
  Main *main_ = nullptr;
  Scene *scene_ = nullptr;
  Object *object_ = nullptr;
  Mesh *mesh_ = nullptr;
  Key *key_ = nullptr;
  Object *omitted_ = nullptr;
  Mesh *omitted_mesh_ = nullptr;
  Key *omitted_key_ = nullptr;
  ReportList reports_{};
  std::string directory_;
  std::vector<int> descriptors_;

  void SetUp() override
  {
    BKE_reports_init(&reports_, RPT_STORE | RPT_PRINT_HANDLED_BY_OWNER);
    char temporary[FILE_MAX];
    BLI_temp_directory_path_get(temporary, sizeof(temporary));
    const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
    for (int i = 0; i < 100 && directory_.empty(); i++) {
      char candidate[FILE_MAX];
      const std::string name = "blender_checkpoint_context_" + std::to_string(nonce) + "_" +
                               std::to_string(i);
      BLI_path_join(candidate, sizeof(candidate), temporary, name.c_str());
      std::error_code error;
      if (std::filesystem::create_directory(
              std::filesystem::path(std::u8string(candidate, candidate + strlen(candidate))),
              error))
      {
        directory_ = candidate;
      }
    }
    ASSERT_FALSE(directory_.empty());
    main_ = BKE_main_new();
    scene_ = BKE_scene_add(main_, "CheckpointContext");
    mesh_ = BKE_mesh_add(main_, "SelectedMesh");
    object_ = BKE_object_add_only_object(main_, OB_MESH, "SelectedObject");
    /* add_only_object starts unused. The fixture retains one ordinary counted owner;
     * tests of explicit unused inclusion reset this count to zero themselves. */
    id_us_plus(&object_->id);
    object_->data = &mesh_->id;
    key_ = BKE_key_add(main_, &mesh_->id);
    mesh_->key = key_;
    BKE_keyblock_add(key_, "Basis");
    BKE_keyblock_add(key_, "SelectedShape");
    object_->mode = OB_MODE_SCULPT;
    object_->shapenr = 2;
    omitted_ = BKE_object_add_only_object(main_, OB_MESH, "OmittedObject");
    omitted_->id.us = 0;
    omitted_mesh_ = BKE_mesh_add(main_, "OmittedMesh");
    omitted_key_ = BKE_key_add(main_, &omitted_mesh_->id);
    omitted_mesh_->key = omitted_key_;
    omitted_mesh_->id.us = 0;
    ASSERT_FALSE(scene_->view_layers.is_empty());
  }

  void TearDown() override
  {
    if (main_) {
      BKE_main_free(main_);
    }
    BKE_reports_free(&reports_);
    for (const int descriptor : descriptors_) {
#ifdef WIN32
      EXPECT_EQ(_close(descriptor), 0);
#else
      EXPECT_EQ(::close(descriptor), 0);
#endif
    }
    if (!directory_.empty()) {
      std::error_code error;
      std::filesystem::remove_all(
          std::filesystem::path(std::u8string(directory_.begin(), directory_.end())), error);
      EXPECT_FALSE(bool(error));
    }
    BlendfileLoadingBaseTest::TearDown();
  }

  std::string path(const char *name) const
  {
    char filepath[FILE_MAX];
    BLI_path_join(filepath, sizeof(filepath), directory_.c_str(), name);
    return filepath;
  }

  int open_base(const char *name)
  {
#ifdef WIN32
    constexpr int binary = O_BINARY;
#else
    constexpr int binary = 0;
#endif
    const int descriptor = BLI_open(path(name).c_str(), O_RDWR | O_CREAT | O_EXCL | binary, 0600);
    if (descriptor >= 0) {
      descriptors_.push_back(descriptor);
    }
    return descriptor;
  }

  std::string bytes(const int descriptor)
  {
    BLI_stat_t info{};
    EXPECT_EQ(BLI_fstat(descriptor, &info), 0);
    if (info.st_size < 0 || info.st_size > 16 * 1024 * 1024) {
      ADD_FAILURE() << "Unexpected fixture stream size";
      return {};
    }
    const int64_t offset = BLI_lseek(descriptor, 0, SEEK_CUR);
    EXPECT_EQ(BLI_lseek(descriptor, 0, SEEK_SET), 0);
    std::string result(size_t(info.st_size), '\0');
    EXPECT_EQ(BLI_read(descriptor, result.data(), result.size()), int64_t(result.size()));
    EXPECT_EQ(BLI_lseek(descriptor, offset, SEEK_SET), offset);
    return result;
  }

  bool write(const int descriptor, const CheckpointWriteContext &context)
  {
    BlendFileWriteParams params;
    params.use_save_as_copy = true;
    return BLO_write_checkpoint_to_descriptor(
        main_, path("logical.blend").c_str(), descriptor, 0, &params, &reports_, context);
  }

  struct StreamEvidence {
    std::vector<BHead> blocks;
    uint64_t global_layer_token = 0;
  };

  StreamEvidence stream_evidence(const std::string &stream)
  {
    StreamEvidence result;
    auto close = [](FileReader *reader) { reader->close(reader); };
    std::unique_ptr<FileReader, decltype(close)> reader(
        BLI_filereader_new_memory(stream.data(), stream.size()), close);
    const BlenderHeaderVariant decoded = BLO_readfile_blender_header_decode(reader.get());
    EXPECT_TRUE(std::holds_alternative<BlenderHeader>(decoded));
    if (!std::holds_alternative<BlenderHeader>(decoded)) {
      return result;
    }
    const BlenderHeader header = std::get<BlenderHeader>(decoded);
    while (size_t(reader->offset) < stream.size()) {
      const auto block = BLO_readfile_read_bhead(reader.get(), header.bhead_type());
      EXPECT_TRUE(block.has_value());
      if (!block || block->len < 0 ||
          uint64_t(block->len) > stream.size() - size_t(reader->offset))
      {
        ADD_FAILURE() << "Incomplete fixture block";
        break;
      }
      result.blocks.push_back(*block);
      if (block->code == BLO_CODE_GLOB) {
        EXPECT_EQ(block->len, sizeof(FileGlobal));
        if (block->len == sizeof(FileGlobal)) {
          FileGlobal global{};
          EXPECT_EQ(reader->read(reader.get(), &global, sizeof(global)), int64_t(sizeof(global)));
          result.global_layer_token = uint64_t(reinterpret_cast<uintptr_t>(global.cur_view_layer));
          continue;
        }
      }
      if (block->code == BLO_CODE_ENDB) {
        break;
      }
      EXPECT_GE(reader->seek(reader.get(), block->len, SEEK_CUR), 0);
    }
    return result;
  }

  void expect_partial_without_completion(const std::string &stream)
  {
    EXPECT_EQ(stream.substr(0, 7), "BLENDER");
    const StreamEvidence evidence = stream_evidence(stream);
    ASSERT_FALSE(evidence.blocks.empty());
    for (const BHead &block : evidence.blocks) {
      EXPECT_NE(block.code, BLO_CODE_DNA1);
      EXPECT_NE(block.code, BLO_CODE_ENDB);
    }
  }

  static bool before_omitted_ids(Main &,
                                 Span<ID *> included,
                                 const CheckpointWriteTokenLookup &tokens,
                                 ReportList *,
                                 void *data)
  {
    auto &f = *static_cast<WritefileCheckpointContextTest *>(data);
    EXPECT_FALSE(included.contains(&f.object_->id));
    EXPECT_FALSE(included.contains(&f.mesh_->id));
    EXPECT_FALSE(included.contains(&f.key_->id));
    EXPECT_FALSE(tokens.id_token(f.object_->id).has_value());
    EXPECT_FALSE(tokens.id_token(f.mesh_->id).has_value());
    EXPECT_FALSE(tokens.id_token(f.key_->id).has_value());
    return true;
  }

  struct CaptureProbe {
    WritefileCheckpointContextTest *fixture;
    bool fail_before = false;
    int wm_descriptor = -1;
    uint64_t early_global_layer_token = 0;
    bool fail_after = false;
    int before_calls = 0;
    int after_calls = 0;
    uint64_t object_token = 0, mesh_token = 0, key_token = 0, scene_token = 0, layer_token = 0;

    static bool include_selected_sculpt(Main &, const ID &id, void *data)
    {
      auto &f = *static_cast<CaptureProbe *>(data)->fixture;
      if (!(f.object_->mode & OB_MODE_SCULPT) || !f.object_->runtime->sculpt_session ||
          f.object_->data != &f.mesh_->id || f.mesh_->key != f.key_)
      {
        return false;
      }
      return &id == &f.object_->id || &id == &f.mesh_->id || &id == &f.key_->id;
    }

    static bool before(Main &main,
                       Span<ID *> included,
                       const CheckpointWriteTokenLookup &tokens,
                       ReportList *,
                       void *data)
    {
      auto &probe = *static_cast<CaptureProbe *>(data);
      auto &f = *probe.fixture;
      probe.before_calls++;
      EXPECT_EQ(&main, f.main_);
      EXPECT_TRUE(included.contains(&f.object_->id));
      EXPECT_TRUE(included.contains(&f.mesh_->id));
      EXPECT_TRUE(included.contains(&f.key_->id));
      EXPECT_FALSE(included.contains(&f.omitted_->id));
      EXPECT_FALSE(tokens.id_token(f.omitted_->id).has_value());
      /* ShapeKey membership follows its actual owner inclusion, even when it has users. */
      EXPECT_FALSE(included.contains(&f.omitted_mesh_->id));
      EXPECT_FALSE(included.contains(&f.omitted_key_->id));
      EXPECT_FALSE(tokens.id_token(f.omitted_mesh_->id).has_value());
      EXPECT_FALSE(tokens.id_token(f.omitted_key_->id).has_value());
      EXPECT_EQ(f.object_->data, &f.mesh_->id);
      EXPECT_EQ(f.mesh_->key, f.key_);
      EXPECT_EQ(f.key_->from, &f.mesh_->id);
      EXPECT_EQ(f.object_->mode, OB_MODE_SCULPT);
      EXPECT_EQ(f.object_->shapenr, 2);
      const auto ob = tokens.id_token(f.object_->id);
      const auto me = tokens.id_token(f.mesh_->id);
      const auto ke = tokens.id_token(f.key_->id);
      const auto sc = tokens.id_token(f.scene_->id);
      EXPECT_TRUE(ob.has_value());
      EXPECT_TRUE(me.has_value());
      EXPECT_TRUE(ke.has_value());
      EXPECT_TRUE(sc.has_value());
      if (!ob || !me || !ke || !sc) {
        return false;
      }
      probe.object_token = *ob;
      probe.mesh_token = *me;
      probe.key_token = *ke;
      probe.scene_token = *sc;
      const ViewLayer &layer = *static_cast<const ViewLayer *>(f.scene_->view_layers.first);
      if (probe.wm_descriptor >= 0) {
        /* GLOB was flushed before this hook. Its pointer remapping created the real token. */
        probe.early_global_layer_token =
            f.stream_evidence(f.bytes(probe.wm_descriptor)).global_layer_token;
        EXPECT_NE(probe.early_global_layer_token, 0u);
      }
      /* Even an existing GLOB-remapped token must not be exported in this phase. */
      EXPECT_FALSE(tokens.view_layer_token(*f.scene_, layer).has_value());
      EXPECT_FALSE(tokens.view_layer_token(*f.scene_, layer).has_value());
      return !probe.fail_before;
    }

    static bool after(Main &,
                      Span<ID *> included,
                      const CheckpointWriteTokenLookup &tokens,
                      ReportList *,
                      void *data)
    {
      auto &probe = *static_cast<CaptureProbe *>(data);
      auto &f = *probe.fixture;
      probe.after_calls++;
      EXPECT_EQ(probe.before_calls, 1);
      EXPECT_TRUE(included.contains(&f.scene_->id));
      EXPECT_EQ(tokens.id_token(f.object_->id), std::optional<uint64_t>(probe.object_token));
      EXPECT_EQ(tokens.id_token(f.mesh_->id), std::optional<uint64_t>(probe.mesh_token));
      EXPECT_EQ(tokens.id_token(f.key_->id), std::optional<uint64_t>(probe.key_token));
      const ViewLayer &layer = *static_cast<const ViewLayer *>(f.scene_->view_layers.first);
      const auto token = tokens.view_layer_token(*f.scene_, layer);
      EXPECT_TRUE(token.has_value());
      if (!token) {
        return false;
      }
      probe.layer_token = *token;
      if (probe.wm_descriptor >= 0) {
        EXPECT_EQ(probe.layer_token, probe.early_global_layer_token);
      }
      ViewLayer unrelated{};
      EXPECT_FALSE(tokens.view_layer_token(*f.scene_, unrelated).has_value());
      return !probe.fail_after;
    }

    CheckpointWriteContext context()
    {
      return {this, before, after};
    }
  };

  void expect_tokens_in_actual_stream(const std::string &stream, const CaptureProbe &probe)
  {
    const StreamEvidence evidence = stream_evidence(stream);
    ASSERT_FALSE(evidence.blocks.empty());
    EXPECT_EQ(evidence.blocks.back().code, BLO_CODE_ENDB);
    const std::set<uint64_t> tokens = {probe.object_token,
                                       probe.mesh_token,
                                       probe.key_token,
                                       probe.scene_token,
                                       probe.layer_token};
    EXPECT_EQ(tokens.size(), 5u);
    EXPECT_FALSE(tokens.contains(0));
    const auto has_block = [&](uint64_t token, int code, int sdna) {
      for (const BHead &block : evidence.blocks) {
        if (uint64_t(reinterpret_cast<uintptr_t>(block.old)) == token && block.code == code &&
            block.SDNAnr == sdna && block.nr == 1)
        {
          return true;
        }
      }
      return false;
    };
    EXPECT_TRUE(has_block(probe.object_token, ID_OB, dna::sdna_struct_id_get<Object>()));
    EXPECT_TRUE(has_block(probe.mesh_token, ID_ME, dna::sdna_struct_id_get<Mesh>()));
    EXPECT_TRUE(has_block(probe.key_token, ID_KE, dna::sdna_struct_id_get<Key>()));
    EXPECT_TRUE(has_block(probe.scene_token, ID_SCE, dna::sdna_struct_id_get<Scene>()));
    EXPECT_TRUE(has_block(probe.layer_token, BLO_CODE_DATA, dna::sdna_struct_id_get<ViewLayer>()));
  }

  void expect_selected_key_rejected(Key &key,
                                     ID &owner,
                                     const bool owner_is_included,
                                     const char *filename)
  {
    struct Selection {
      Key *key;
      ID *owner;
      bool owner_is_included;
      int queries = 0, before_calls = 0, after_calls = 0;
    } selection{&key, &owner, owner_is_included};
    CheckpointWriteContext context;
    context.user_data = &selection;
    context.include_id = [](Main &, const ID &id, void *data) {
      auto &selected = *static_cast<Selection *>(data);
      if (&id != &selected.key->id) {
        return false;
      }
      selected.queries++;
      /* An orphan Key is not silently repaired by the pre-save Mesh->Key validation. */
      EXPECT_EQ(selected.key->from, selected.owner);
      return true;
    };
    context.before_local_ids = [](Main &,
                                  Span<ID *> included,
                                  const CheckpointWriteTokenLookup &tokens,
                                  ReportList *,
                                  void *data) {
      auto &selected = *static_cast<Selection *>(data);
      selected.before_calls++;
      EXPECT_GT(selected.queries, 0);
      EXPECT_EQ(selected.key->from, selected.owner);
      EXPECT_EQ(included.contains(selected.owner), selected.owner_is_included);
      EXPECT_FALSE(included.contains(&selected.key->id));
      EXPECT_FALSE(tokens.id_token(selected.key->id).has_value());
      return true;
    };
    context.after_local_ids =
        [](Main &, Span<ID *> included, const CheckpointWriteTokenLookup &tokens, ReportList *,
           void *data) {
          auto &selected = *static_cast<Selection *>(data);
          selected.after_calls++;
          EXPECT_FALSE(included.contains(&selected.key->id));
          EXPECT_FALSE(tokens.id_token(selected.key->id).has_value());
          return true;
        };
    const int descriptor = open_base(filename);
    ASSERT_GE(descriptor, 0);
    ASSERT_TRUE(write(descriptor, context));
    EXPECT_EQ(selection.before_calls, 1);
    EXPECT_EQ(selection.after_calls, 1);
    EXPECT_EQ(key.id.us, 0);
    int written_keys = 0;
    for (const BHead &block : stream_evidence(bytes(descriptor)).blocks) {
      written_keys += block.code == ID_KE;
    }
    EXPECT_EQ(written_keys, 1); /* Only the fixture's original used Key. */
  }
};

TEST_F(WritefileCheckpointContextTest, IncludedRelationsAndLateViewLayerUseActualWrittenTokens)
{
  const int descriptor = open_base("complete.blend");
  ASSERT_GE(descriptor, 0);
  CaptureProbe probe{this};
  ASSERT_TRUE(write(descriptor, probe.context()));
  EXPECT_EQ(probe.before_calls, 1);
  EXPECT_EQ(probe.after_calls, 1);
  expect_tokens_in_actual_stream(bytes(descriptor), probe);
  EXPECT_EQ(object_->data, &mesh_->id);
  EXPECT_EQ(mesh_->key, key_);
  EXPECT_EQ(object_->mode, OB_MODE_SCULPT);
  EXPECT_EQ(object_->shapenr, 2);
}

TEST_F(WritefileCheckpointContextTest, WindowGlobalTokenExistsButViewLayerExportIsLateOnly)
{
  /* A real WM/window/workspace binding makes write_global remap cur_view_layer before capture. */
  WorkSpace *workspace = BKE_workspace_add(main_, "CheckpointWorkspace");
  bScreen *screen = BKE_id_new<bScreen>(main_, "CheckpointScreen");
  BKE_workspace_layout_add(main_, *workspace, *screen, "CheckpointLayout");
  wmWindowManager *manager = BKE_id_new<wmWindowManager>(main_, "CheckpointWM");
  manager->runtime = MEM_new<bke::WindowManagerRuntime>(__func__);
  wmWindow *window = wm_window_new(main_, manager, nullptr, false);
  BKE_workspace_active_set(window->workspace_hook, workspace);
  window->scene = scene_;
  const ViewLayer &layer = *static_cast<const ViewLayer *>(scene_->view_layers.first);
  STRNCPY(window->view_layer_name, layer.name);
  const int descriptor = open_base("wm-global.blend");
  ASSERT_GE(descriptor, 0);
  CaptureProbe probe{this};
  probe.wm_descriptor = descriptor;
  ASSERT_TRUE(write(descriptor, probe.context()));
  EXPECT_EQ(probe.before_calls, 1);
  EXPECT_EQ(probe.after_calls, 1);
  EXPECT_NE(probe.early_global_layer_token, 0u);
  EXPECT_EQ(probe.early_global_layer_token, probe.layer_token);
  expect_tokens_in_actual_stream(bytes(descriptor), probe);
}

TEST_F(WritefileCheckpointContextTest, FinalPreparedRelationCanHaveANullableKey)
{
  Mesh *replacement = BKE_mesh_add(main_, "FinalSelectedMesh");
  object_->data = &replacement->id;
  object_->shapenr = 0;
  const int descriptor = open_base("nullable-key.blend");
  ASSERT_GE(descriptor, 0);
  struct Selection {
    Object *object;
    Mesh *mesh;
    int calls = 0;
  } selected{object_, replacement};
  CheckpointWriteContext context;
  context.user_data = &selected;
  context.before_local_ids = [](Main &,
                                Span<ID *> included,
                                const CheckpointWriteTokenLookup &tokens,
                                ReportList *,
                                void *data) {
    auto &selection = *static_cast<Selection *>(data);
    selection.calls++;
    EXPECT_EQ(selection.object->data, &selection.mesh->id);
    EXPECT_EQ(selection.mesh->key, nullptr);
    EXPECT_EQ(selection.object->shapenr, 0);
    EXPECT_TRUE(included.contains(&selection.mesh->id));
    return tokens.id_token(selection.object->id).has_value() &&
           tokens.id_token(selection.mesh->id).has_value();
  };
  context.after_local_ids =
      [](Main &, Span<ID *>, const CheckpointWriteTokenLookup &, ReportList *, void *data) {
        auto &selection = *static_cast<Selection *>(data);
        selection.calls++;
        return selection.object->data == &selection.mesh->id && selection.mesh->key == nullptr;
      };
  EXPECT_TRUE(write(descriptor, context));
  EXPECT_EQ(selected.calls, 2);
  EXPECT_EQ(object_->data, &replacement->id);
}

TEST_F(WritefileCheckpointContextTest,
       UnusedSelectedSculptRelationsAreIncludedWithoutChangingUsers)
{
  object_->runtime->sculpt_session = MEM_new<SculptSession>(__func__);
  object_->id.us = mesh_->id.us = key_->id.us = 0;
  const auto object_flags = object_->id.flag;
  const auto mesh_flags = mesh_->id.flag;
  const auto key_flags = key_->id.flag;
  const int descriptor = open_base("unused-selected.blend");
  ASSERT_GE(descriptor, 0);
  CaptureProbe probe{this};
  CheckpointWriteContext context = probe.context();
  context.include_id = CaptureProbe::include_selected_sculpt;
  ASSERT_TRUE(write(descriptor, context));
  expect_tokens_in_actual_stream(bytes(descriptor), probe);
  EXPECT_EQ(probe.before_calls, 1);
  EXPECT_EQ(probe.after_calls, 1);
  EXPECT_EQ(object_->id.us, 0);
  EXPECT_EQ(mesh_->id.us, 0);
  EXPECT_EQ(key_->id.us, 0);
  EXPECT_EQ(object_->id.flag, object_flags);
  EXPECT_EQ(mesh_->id.flag, mesh_flags);
  EXPECT_EQ(key_->id.flag, key_flags);
}

TEST_F(WritefileCheckpointContextTest, NoRuntimeSelectionKeepsOrdinaryUnusedInclusionAndBytes)
{
  object_->mode = OB_MODE_OBJECT;
  object_->id.us = mesh_->id.us = key_->id.us = 0;
  CheckpointWriteContext context;
  context.user_data = this;
  context.include_id = [](Main &, const ID &, void *) { return false; };
  context.before_local_ids = before_omitted_ids;
  context.after_local_ids =
      [](Main &, Span<ID *>, const CheckpointWriteTokenLookup &, ReportList *, void *) {
        return true;
      };
  const int private_fd = open_base("no-runtime-private.blend");
  const int ordinary_fd = open_base("no-runtime-ordinary.blend");
  ASSERT_GE(private_fd, 0);
  ASSERT_GE(ordinary_fd, 0);
  ASSERT_TRUE(write(private_fd, context));
  BlendFileWriteParams params;
  params.use_save_as_copy = true;
  ASSERT_TRUE(BLO_write_file_to_descriptor(
      main_, path("logical.blend").c_str(), ordinary_fd, 0, &params, &reports_));
  EXPECT_EQ(bytes(private_fd), bytes(ordinary_fd));
  for (const BHead &block : stream_evidence(bytes(private_fd)).blocks) {
    EXPECT_NE(block.code, ID_OB);
    EXPECT_NE(block.code, ID_ME);
    EXPECT_NE(block.code, ID_KE);
  }
}

TEST_F(WritefileCheckpointContextTest, SelectionCannotBypassLinkedRuntimeOrOtherTypeExclusions)
{
  omitted_->id.tag |= ID_TAG_RUNTIME;
  Object *linked = BKE_object_add_only_object(main_, OB_MESH, "LinkedNotSelected");
  Library *library = BKE_id_new<Library>(main_, "SelectionLibrary");
  linked->id.lib = library;
  linked->id.us = 0;
  Material *material = BKE_id_new<Material>(main_, "UnusedOtherType");
  material->id.us = 0;
  auto free_outside = [](Mesh *mesh) { BKE_id_free(nullptr, mesh); };
  std::unique_ptr<Mesh, decltype(free_outside)> outside(BKE_id_new_nomain<Mesh>("NotInMain"),
                                                        free_outside);
  ASSERT_NE(outside, nullptr);
  struct Selection {
    ID *runtime, *linked, *other, *outside;
    int rejected_predicate_calls = 0;
  } selection{&omitted_->id, &linked->id, &material->id, &outside->id};
  CheckpointWriteContext context;
  context.user_data = &selection;
  context.include_id = [](Main &, const ID &id, void *data) {
    auto &selected = *static_cast<Selection *>(data);
    if (&id == selected.runtime || &id == selected.linked || &id == selected.other ||
        &id == selected.outside)
    {
      selected.rejected_predicate_calls++;
    }
    return true; /* Deliberately overbroad predicate tests the writer's hard eligibility gates. */
  };
  context.before_local_ids = [](Main &,
                                Span<ID *> included,
                                const CheckpointWriteTokenLookup &tokens,
                                ReportList *,
                                void *data) {
    auto &selected = *static_cast<Selection *>(data);
    EXPECT_EQ(selected.rejected_predicate_calls, 0);
    for (ID *id : {selected.runtime, selected.linked, selected.other, selected.outside}) {
      EXPECT_FALSE(included.contains(id));
      EXPECT_FALSE(tokens.id_token(*id).has_value());
    }
    return true;
  };
  context.after_local_ids =
      [](Main &, Span<ID *>, const CheckpointWriteTokenLookup &, ReportList *, void *) {
        return true;
      };
  const int descriptor = open_base("eligibility.blend");
  ASSERT_GE(descriptor, 0);
  EXPECT_TRUE(write(descriptor, context));
  EXPECT_EQ(selection.rejected_predicate_calls, 0);
}

TEST_F(WritefileCheckpointContextTest, SelectedKeyRequiresOwnerInFinalIncludedMainIds)
{
  auto free_outside = [](Mesh *mesh) { BKE_id_free(nullptr, mesh); };
  std::unique_ptr<Mesh, decltype(free_outside)> outside(BKE_id_new_nomain<Mesh>("OutsideKeyOwner"),
                                                        free_outside);
  ASSERT_NE(outside, nullptr);
  Key *orphan = BKE_key_add(main_, &outside->id);
  orphan->id.us = 0;
  struct RestoreOwner {
    Key &key;
    ID &owner;
    ~RestoreOwner()
    {
      key.from = &owner;
    }
  } restore{*orphan, mesh_->id}; /* Keep the pointer valid during later Main destruction. */
  expect_selected_key_rejected(*orphan, outside->id, false, "foreign-key-owner.blend");
}

TEST_F(WritefileCheckpointContextTest, SelectedKeyRequiresReciprocalMeshKey)
{
  Key *orphan = BKE_key_add(main_, &mesh_->id);
  orphan->id.us = 0;
  ASSERT_NE(mesh_->key, orphan);
  expect_selected_key_rejected(*orphan, mesh_->id, true, "nonreciprocal-key.blend");
  EXPECT_EQ(mesh_->key, key_);
  EXPECT_EQ(orphan->from, &mesh_->id);
}

TEST_F(WritefileCheckpointContextTest, OverrideRestorationRecomputesSelectedUnusedRelations)
{
  Library *library = BKE_id_new<Library>(main_, "OverrideSelectionLibrary");
  Mesh *reference_mesh = BKE_id_new_in_lib<Mesh>(main_, library, "ReferenceMesh");
  Object *reference = BKE_id_new_in_lib<Object>(main_, library, "ReferenceObject");
  reference->type = OB_MESH;
  reference_mesh->id.us = 0;
  BKE_mesh_assign_object(main_, reference, reference_mesh);
  Object *local = id_cast<Object *>(
      BKE_lib_override_library_create_from_id(main_, &reference->id, false));
  ASSERT_NE(local, nullptr);
  Mesh *local_mesh = BKE_mesh_add(main_, "InitiallySelectedMesh");
  local_mesh->id.us = 0;
  BKE_mesh_assign_object(main_, local, local_mesh);
  Key *local_key = BKE_key_add(main_, &local_mesh->id);
  local_mesh->key = local_key;
  BKE_keyblock_add(local_key, "Basis");
  local->mode = OB_MODE_SCULPT;
  local->runtime->sculpt_session = MEM_new<SculptSession>(__func__);
  local->id.us = local_key->id.us = 0;
  ASSERT_EQ(local_mesh->id.us, 1);
  IDOverrideLibraryProperty *property = BKE_lib_override_library_property_get(
      local->id.override_library, "data", nullptr);
  ASSERT_NE(BKE_lib_override_library_property_operation_get(property,
                                                           LIBOVERRIDE_OP_NOOP,
                                                           nullptr,
                                                           nullptr,
                                                           {},
                                                           {},
                                                           -1,
                                                           -1,
                                                           true,
                                                           nullptr,
                                                           nullptr),
            nullptr);
  struct Selection {
    Object *object;
    Mesh *old_mesh, *reference_mesh;
    Key *old_key, *ordinary_key;
    int old_key_selected = 0, before_calls = 0, after_calls = 0;
    uint64_t object_token = 0, ordinary_key_token = 0;
  } selection{local, local_mesh, reference_mesh, local_key, key_};
  CheckpointWriteContext context;
  context.user_data = &selection;
  context.include_id = [](Main &, const ID &id, void *data) {
    auto &selected = *static_cast<Selection *>(data);
    if (&id == &selected.object->id || &id == selected.object->data) {
      return true;
    }
    const Mesh *current_mesh = id_cast<Mesh *>(selected.object->data);
    if (current_mesh->key && &id == &current_mesh->key->id) {
      selected.old_key_selected += &id == &selected.old_key->id;
      return true;
    }
    return false;
  };
  context.before_local_ids = [](Main &,
                                Span<ID *> included,
                                const CheckpointWriteTokenLookup &tokens,
                                ReportList *,
                                void *data) {
    auto &selected = *static_cast<Selection *>(data);
    selected.before_calls++;
    EXPECT_GT(selected.old_key_selected, 0);
    EXPECT_EQ(selected.object->data, &selected.reference_mesh->id);
    EXPECT_TRUE(included.contains(&selected.object->id));
    /* Ordinary inclusion is retained; only stale checkpoint extras must be discarded. */
    EXPECT_TRUE(included.contains(&selected.old_mesh->id));
    EXPECT_FALSE(included.contains(&selected.old_key->id));
    EXPECT_FALSE(tokens.id_token(selected.old_key->id).has_value());
    selected.object_token = tokens.id_token(selected.object->id).value_or(0);
    selected.ordinary_key_token = tokens.id_token(selected.ordinary_key->id).value_or(0);
    EXPECT_NE(selected.object_token, 0u);
    EXPECT_NE(selected.ordinary_key_token, 0u);
    return true;
  };
  context.after_local_ids =
      [](Main &, Span<ID *> included, const CheckpointWriteTokenLookup &tokens, ReportList *,
         void *data) {
        auto &selected = *static_cast<Selection *>(data);
        selected.after_calls++;
        EXPECT_FALSE(included.contains(&selected.old_key->id));
        EXPECT_FALSE(tokens.id_token(selected.old_key->id).has_value());
        EXPECT_EQ(tokens.id_token(selected.object->id),
                  std::optional<uint64_t>(selected.object_token));
        return true;
      };
  const int descriptor = open_base("override-selection.blend");
  ASSERT_GE(descriptor, 0);
  struct RestoreGlobalMain {
    Main *previous;
    ~RestoreGlobalMain()
    {
      BKE_blender_globals_main_swap(previous);
    }
  } restore{BKE_blender_globals_main_swap(main_)};
  ASSERT_TRUE(write(descriptor, context));
  EXPECT_EQ(selection.before_calls, 1);
  EXPECT_EQ(selection.after_calls, 1);
  EXPECT_EQ(local->id.us, 0);
  EXPECT_EQ(local_key->id.us, 0);
  EXPECT_EQ(local->data, &reference_mesh->id);
  int written_keys = 0;
  bool wrote_selected_object = false;
  for (const BHead &block : stream_evidence(bytes(descriptor)).blocks) {
    const uint64_t token = uint64_t(reinterpret_cast<uintptr_t>(block.old));
    if (block.code == ID_KE) {
      written_keys++;
      EXPECT_EQ(token, selection.ordinary_key_token);
      EXPECT_EQ(block.SDNAnr, dna::sdna_struct_id_get<Key>());
    }
    if (token == selection.object_token && block.code == ID_OB &&
        block.SDNAnr == dna::sdna_struct_id_get<Object>())
    {
      wrote_selected_object = true;
    }
  }
  EXPECT_EQ(written_keys, 1);
  EXPECT_TRUE(wrote_selected_object);
}

TEST_F(WritefileCheckpointContextTest, BeforeFailureLeavesHeadersAndOpenPartialBase)
{
  const int descriptor = open_base("before-failed.blend");
  ASSERT_GE(descriptor, 0);
  CaptureProbe probe{this};
  probe.fail_before = true;
  EXPECT_FALSE(write(descriptor, probe.context()));
  EXPECT_EQ(probe.before_calls, 1);
  EXPECT_EQ(probe.after_calls, 0);
  const std::string prefix = bytes(descriptor);
  expect_partial_without_completion(prefix);
  EXPECT_GT(BLI_lseek(descriptor, 0, SEEK_CUR), 0);
}

TEST_F(WritefileCheckpointContextTest, LateFailureRetainsPartialStreamWithoutClaimingCompletion)
{
  const int descriptor = open_base("late-failed.blend");
  ASSERT_GE(descriptor, 0);
  CaptureProbe probe{this};
  probe.fail_after = true;
  EXPECT_FALSE(write(descriptor, probe.context()));
  EXPECT_EQ(probe.before_calls, 1);
  EXPECT_EQ(probe.after_calls, 1);
  expect_partial_without_completion(bytes(descriptor));
}

TEST_F(WritefileCheckpointContextTest, MissingHookRefusesBeforePreparationOrBytes)
{
  const int descriptor = open_base("missing-hook.blend");
  ASSERT_GE(descriptor, 0);
  scene_->id.flag |= ID_FLAG_CLIPBOARD_MARK;
  CaptureProbe probe{this};
  CheckpointWriteContext context = probe.context();
  context.after_local_ids = nullptr;
  EXPECT_FALSE(write(descriptor, context));
  EXPECT_TRUE(bytes(descriptor).empty());
  EXPECT_TRUE(scene_->id.flag & ID_FLAG_CLIPBOARD_MARK);
  EXPECT_EQ(probe.before_calls, 0);
}

TEST_F(WritefileCheckpointContextTest, PrivateContextRetainsOrdinaryDescriptorAdmission)
{
  const int descriptor = open_base("no-remap.blend");
  ASSERT_GE(descriptor, 0);
  CaptureProbe probe{this};
  BlendFileWriteParams params;
  params.use_save_as_copy = true;
  params.remap_mode = BLO_WRITE_PATH_REMAP_RELATIVE;
  EXPECT_FALSE(BLO_write_checkpoint_to_descriptor(
      main_, path("logical.blend").c_str(), descriptor, 0, &params, &reports_, probe.context()));
  EXPECT_TRUE(bytes(descriptor).empty());
  EXPECT_EQ(probe.before_calls, 0);
}

}  // namespace
}  // namespace blender
