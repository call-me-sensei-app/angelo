/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <sstream>
#include <limits>
#include <memory>
#include <optional>
#include <utility>
#include <string>

#include "BKE_idprop.hh"
#include "BKE_global.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_report.hh"
#include "BKE_scene.hh"
#include "BKE_wm_runtime.hh"
#include "BKE_workspace.hh"

#include "DNA_collection_types.h"
#include "DNA_fileglobal_types.h"
#include "DNA_scene_types.h"
#include "DNA_screen_types.h"
#include "DNA_sdna_type_ids.hh"
#include "DNA_windowmanager_types.h"
#include "DNA_workspace_types.h"

#include "BLI_compiler_compat.h"
#include "BLI_listbase.h"
#include "BLI_memory_utils.hh"
#include "BLI_string.h"

#include "MEM_guardedalloc.h"

#include "wm_window.hh"
#include "../../windowmanager/intern/wm_checkpoint.hh"

#include "BLO_read_write.hh"
#include "BLO_core_bhead.hh"
#include "BLO_core_blend_header.hh"
#include "BLI_filereader.h"
#include "BLO_undofile.hh"
#include "BLO_writefile.hh"

#include "../intern/writefile.hh"
#include "blendfile_loading_base_test.h"

namespace blender {
namespace {

static const MemFileChunk *first_chunk(const MemFile &file)
{
  return static_cast<const MemFileChunk *>(file.chunks.first);
}

struct OwnedMemFile {
  MemFile file{};
  ~OwnedMemFile()
  {
    BLO_memfile_free(&file);
  }
};

static std::string memfile_bytes(const MemFile &file)
{
  std::string bytes;
  for (const MemFileChunk *chunk = first_chunk(file); chunk;
       chunk = static_cast<const MemFileChunk *>(chunk->next))
  {
    bytes.append(chunk->buf, size_t(chunk->size));
  }
  return bytes;
}

static BLI_NOINLINE BlendMemFileWriteResult write_witness_at_depth(Main *main,
                                                                MemFile *file,
                                                                const int depth)
{
  /* A live volatile value read after recursion prevents tail-call elimination. Every recursive
   * frame must remain present while the writer creates its temporary metadata. */
  volatile int live_depth = depth;
  const BlendMemFileWriteResult result = depth ? write_witness_at_depth(main, file, depth - 1) :
      BLO_write_file_mem_bounded(main, file, 0, 256 * 1024 * 1024);
  EXPECT_EQ(live_depth, depth);
  return result;
}

TEST(WritefileMemBudget, checked_arithmetic_and_sticky_refusal)
{
  MemFileWriteBudget budget{12};
  EXPECT_TRUE(budget.charge_product(3, 4));
  EXPECT_FALSE(budget.charge(1));
  EXPECT_EQ(budget.accounted, 12u);
  EXPECT_FALSE(budget.charge(0));
  EXPECT_EQ(budget.accounted, 12u);
  MemFileWriteBudget overflow{SIZE_MAX};
  EXPECT_FALSE(overflow.charge_product(SIZE_MAX, 2));
  EXPECT_EQ(overflow.accounted, 0u);
  EXPECT_TRUE(overflow.failed);
}

TEST(WritefileMemBudget, hash_table_growth_refuses_before_capacity_changes)
{
  Map<uint64_t, uint64_t> map;
  const int64_t old_capacity = map.capacity();
  MemFileWriteBudget refused{0};
  EXPECT_FALSE(BLO_memfile_budget_reserve(map, &refused, 128));
  EXPECT_EQ(map.capacity(), old_capacity);
  EXPECT_TRUE(map.is_empty());
  EXPECT_EQ(refused.accounted, 0u);
  EXPECT_FALSE(BLO_memfile_budget_reserve(map, &refused, 128));
  EXPECT_EQ(map.capacity(), old_capacity);

  MemFileWriteBudget accepted{1024 * 1024};
  EXPECT_TRUE(BLO_memfile_budget_reserve(map, &accepted, 128));
  EXPECT_EQ(accepted.accounted, size_t(map.size_in_bytes()));
  const size_t charged = accepted.accounted;
  EXPECT_TRUE(BLO_memfile_budget_reserve(map, &accepted, 100));
  EXPECT_EQ(accepted.accounted, charged);

  Set<uint64_t> set;
  EXPECT_TRUE(BLO_memfile_budget_reserve(set, &accepted, 128));
  EXPECT_EQ(accepted.accounted - charged, size_t(set.size_in_bytes()));
}

TEST(WritefileMemBudget, vector_growth_exact_boundary_and_reuse)
{
  Vector<uint64_t, 4> values;
  const size_t growth = 8 * sizeof(uint64_t);
  MemFileWriteBudget refused{growth - 1};
  EXPECT_FALSE(BLO_memfile_budget_reserve(values, &refused, 5));
  EXPECT_EQ(values.capacity(), 4);
  MemFileWriteBudget accepted{growth};
  EXPECT_TRUE(BLO_memfile_budget_reserve(values, &accepted, 5));
  EXPECT_EQ(values.capacity(), 8);
  EXPECT_EQ(accepted.accounted, growth);
  EXPECT_TRUE(BLO_memfile_budget_reserve(values, &accepted, 8));
  EXPECT_EQ(accepted.accounted, growth);
  EXPECT_FALSE(BLO_memfile_budget_reserve(values, &accepted, INT64_MAX));
  EXPECT_EQ(values.capacity(), 8);
}

TEST(WritefileMemBudget, chunk_payload_and_record_admission_precede_allocation)
{
  const char payload[] = "budget chunk";
  OwnedMemFile output;
  MemFileWriteData data{};
  data.written_memfile = &output.file;
  MemFileWriteBudget refused{sizeof(MemFileChunk) + sizeof(payload) - 1};
  data.budget = &refused;
  BLO_memfile_chunk_add(&data, payload, sizeof(payload));
  EXPECT_TRUE(refused.failed);
  EXPECT_TRUE(output.file.chunks.is_empty());
  EXPECT_EQ(output.file.size, 0u);
  const size_t charged = refused.accounted;
  BLO_memfile_chunk_add(&data, payload, sizeof(payload));
  EXPECT_EQ(refused.accounted, charged);
  EXPECT_TRUE(output.file.chunks.is_empty());

  MemFileWriteBudget accepted{sizeof(MemFileChunk) + sizeof(payload)};
  data.budget = &accepted;
  BLO_memfile_chunk_add(&data, payload, sizeof(payload));
  EXPECT_FALSE(accepted.failed);
  EXPECT_EQ(accepted.accounted, accepted.limit);
  ASSERT_NE(output.file.chunks.first, nullptr);
  EXPECT_EQ(std::memcmp(first_chunk(output.file)->buf, payload, sizeof(payload)), 0);
  EXPECT_EQ(output.file.size, sizeof(payload));
}

TEST(WritefileMemBudget, identical_chunk_charges_only_new_record)
{
  const char payload[] = "retained chunk";
  OwnedMemFile reference, output;
  MemFileWriteData original{};
  original.written_memfile = &reference.file;
  BLO_memfile_chunk_add(&original, payload, sizeof(payload));
  MemFileWriteBudget budget{sizeof(MemFileChunk)};
  MemFileWriteData data{};
  data.budget = &budget;
  data.written_memfile = &output.file;
  data.reference_current_chunk = static_cast<MemFileChunk *>(reference.file.chunks.first);
  BLO_memfile_chunk_add(&data, payload, sizeof(payload));
  ASSERT_NE(output.file.chunks.first, nullptr);
  EXPECT_TRUE(first_chunk(output.file)->is_identical);
  EXPECT_EQ(first_chunk(output.file)->buf, first_chunk(reference.file)->buf);
  EXPECT_EQ(budget.accounted, sizeof(MemFileChunk));
  EXPECT_EQ(output.file.size, 0u);
}

TEST(WritefileMemBudget, shared_retention_is_metadata_only_and_cleanup_drops_one_user)
{
  auto remove_owner = [](ImplicitSharedValue<int> *owner) {
    owner->remove_user_and_delete_if_last();
  };
  std::unique_ptr<ImplicitSharedValue<int>, decltype(remove_owner)> owner(
      new ImplicitSharedValue<int>(42), remove_owner);
  OwnedMemFile output;
  MemFileWriteBudget budget{64 * 1024};
  WriteData wd{};
  wd.use_memfile = true;
  wd.mem.budget = &budget;
  wd.mem.written_memfile = &output.file;
  BlendWriter writer{&wd};
  bool copied = false;
  BLO_write_shared(&writer, &owner->data, SIZE_MAX / 4, owner.get(), [&]() { copied = true; });
  EXPECT_FALSE(copied);
  EXPECT_FALSE(budget.failed);
  EXPECT_LT(budget.accounted, 64u * 1024);
  EXPECT_EQ(owner->strong_users(), 2);
  EXPECT_GT(output.file.size, budget.accounted);
  budget.limit = budget.accounted;
  const char next[] = "refuse after retention";
  BLO_memfile_chunk_add(&wd.mem, next, sizeof(next));
  EXPECT_TRUE(budget.failed);
  EXPECT_TRUE(output.file.chunks.is_empty());
  BLO_memfile_free(&output.file);
  EXPECT_EQ(owner->strong_users(), 1);
  EXPECT_EQ(output.file.shared_storage, nullptr);
  BLO_memfile_free(&output.file);
  EXPECT_EQ(owner->strong_users(), 1);
}

TEST(WritefileMemBudget, failed_shared_owner_admission_does_not_acquire_user)
{
  auto *owner = new ImplicitSharedValue<int>(42);
  OwnedMemFile output;
  MemFileWriteBudget budget{0};
  WriteData wd{};
  wd.use_memfile = true;
  wd.mem.budget = &budget;
  wd.mem.written_memfile = &output.file;
  BlendWriter writer{&wd};
  bool copied = false;
  BLO_write_shared(&writer, &owner->data, 1024, owner, [&]() { copied = true; });
  EXPECT_FALSE(copied);
  EXPECT_TRUE(wd.validation_data.critical_error);
  EXPECT_EQ(output.file.shared_storage, nullptr);
  EXPECT_EQ(owner->strong_users(), 1);
  owner->remove_user_and_delete_if_last();
}

TEST(WritefileMemBudget, scalar_array_counts_refuse_before_access_or_allocation)
{
  OwnedMemFile output;
  MemFileWriteBudget budget{SIZE_MAX};
  WriteData wd{};
  wd.use_memfile = true;
  wd.mem.budget = &budget;
  wd.mem.written_memfile = &output.file;
  BlendWriter writer{&wd};
  const int16_t small = 1;
  writer.write_int16_array(-1, &small);
  EXPECT_TRUE(budget.failed);
  EXPECT_TRUE(wd.validation_data.critical_error);
  EXPECT_TRUE(output.file.chunks.is_empty());
  EXPECT_EQ(budget.accounted, 0u);

  MemFileWriteBudget overflow{SIZE_MAX};
  wd.mem.budget = &overflow;
  wd.validation_data.critical_error = false;
  const float single = 1.0f;
  writer.write_float3_array(INT64_MAX, &single);
  EXPECT_TRUE(overflow.failed);
  EXPECT_TRUE(output.file.chunks.is_empty());
  EXPECT_EQ(overflow.accounted, 0u);
}

class WritefileMemBudgetTest : public BlendfileLoadingBaseTest {
 protected:
  Main *main_ = nullptr;
  Scene *scene_ = nullptr;

  void SetUp() override
  {
    main_ = BKE_main_new();
    scene_ = BKE_scene_add(main_, "Budget fixture");
    const std::string payload(256 * 1024, 'x');
    ASSERT_TRUE(IDP_AddToGroup(IDP_EnsureProperties(&scene_->id),
                              IDP_NewString(payload.c_str(), "budget_payload")));
    /* The Undo writer's normal recalc resets are the caller's preservation responsibility.
     * Zero initial fields make ordinary/bounded serialization parity compare stable inputs. */
    scene_->id.recalc_up_to_undo_push = 0;
    scene_->id.recalc_after_undo_push = 0;
    scene_->master_collection->id.recalc_up_to_undo_push = 0;
    scene_->master_collection->id.recalc_after_undo_push = 0;
  }

  void TearDown() override
  {
    BKE_main_free(main_);
    BlendfileLoadingBaseTest::TearDown();
  }

  static std::optional<std::pair<size_t, size_t>> global_header_address(const MemFile &file)
  {
    std::string stream;
    for (const MemFileChunk *chunk = first_chunk(file); chunk;
         chunk = static_cast<const MemFileChunk *>(chunk->next))
    {
      stream.append(chunk->buf, size_t(chunk->size));
    }
    auto close = [](FileReader *reader) { reader->close(reader); };
    std::unique_ptr<FileReader, decltype(close)> reader(
        BLI_filereader_new_memory(stream.data(), stream.size()), close);
    const auto decoded = BLO_readfile_blender_header_decode(reader.get());
    EXPECT_TRUE(std::holds_alternative<BlenderHeader>(decoded));
    if (!std::holds_alternative<BlenderHeader>(decoded)) {
      return std::nullopt;
    }
    const auto header = std::get<BlenderHeader>(decoded);
    std::optional<std::pair<size_t, size_t>> address;
    bool complete = false;
    while (size_t(reader->offset) < stream.size()) {
      const size_t block_start = size_t(reader->offset);
      const auto block = BLO_readfile_read_bhead(reader.get(), header.bhead_type());
      if (!block || block->len < 0 || size_t(block->len) > stream.size() - size_t(reader->offset)) {
        ADD_FAILURE() << "Invalid MemFile block while locating FileGlobal header";
        return std::nullopt;
      }
      if (block->code == BLO_CODE_GLOB) {
        EXPECT_FALSE(address.has_value());
        EXPECT_EQ(block->SDNAnr, dna::sdna_struct_id_get<FileGlobal>());
        EXPECT_EQ(block->nr, 1);
        EXPECT_EQ(block->len, sizeof(FileGlobal));
        EXPECT_NE(block->old, nullptr);
        if (address || block->SDNAnr != dna::sdna_struct_id_get<FileGlobal>() ||
            block->nr != 1 || block->len != sizeof(FileGlobal) || !block->old)
        {
          return std::nullopt;
        }
        switch (header.bhead_type()) {
          case BHeadType::BHead4:
            address = std::pair{block_start + offsetof(BHead4, old), sizeof(BHead4::old)};
            break;
          case BHeadType::SmallBHead8:
            address = std::pair{block_start + offsetof(SmallBHead8, old), sizeof(SmallBHead8::old)};
            break;
          case BHeadType::LargeBHead8:
            address = std::pair{block_start + offsetof(LargeBHead8, old), sizeof(LargeBHead8::old)};
            break;
        }
      }
      if (block->code == BLO_CODE_ENDB) {
        complete = true;
        break;
      }
      if (reader->seek(reader.get(), block->len, SEEK_CUR) < 0) {
        ADD_FAILURE() << "Cannot advance through MemFile block";
        return std::nullopt;
      }
    }
    EXPECT_TRUE(complete);
    EXPECT_TRUE(address.has_value());
    return complete ? address : std::nullopt;
  }

  static void expect_same(const MemFile &a, const MemFile &b)
  {
    /* write_global serializes a stack-local FileGlobal. Undo keeps its BHead.old address,
     * which differs across public API call frames. read_global consumes the payload directly
     * and never inserts that address into a remapping table. Compare all payload/pointer bytes
     * and every other header byte exactly; exclude only this single parsed, unreferenced slot. */
    const auto global_a = global_header_address(a);
    const auto global_b = global_header_address(b);
    ASSERT_TRUE(global_a && global_b);
    ASSERT_EQ(*global_a, *global_b);
    const size_t ignored_start = global_a->first;
    const size_t ignored_end = ignored_start + global_a->second;
    size_t ignored_bytes = 0;
    const MemFileChunk *left = first_chunk(a);
    const MemFileChunk *right = first_chunk(b);
    size_t chunk_index = 0;
    size_t stream_offset = 0;
    while (left && right) {
      ASSERT_EQ(left->size, right->size);
      EXPECT_EQ(left->id_session_uid, right->id_session_uid);
      const size_t end = stream_offset + size_t(left->size);
      const size_t prefix = std::clamp(ignored_start, stream_offset, end) - stream_offset;
      const size_t suffix = std::clamp(ignored_end, stream_offset, end) - stream_offset;
      ignored_bytes += suffix - prefix;
      const int before = std::memcmp(left->buf, right->buf, prefix);
      const int after = std::memcmp(left->buf + suffix, right->buf + suffix,
                                    size_t(left->size) - suffix);
      const int comparison = before != 0 ? before : after;
      if (comparison != 0) {
        size_t first_difference = 0;
        while (first_difference < size_t(left->size) &&
               (left->buf[first_difference] == right->buf[first_difference] ||
                (stream_offset + first_difference >= ignored_start &&
                 stream_offset + first_difference < ignored_end)))
        {
          first_difference++;
        }
        std::ostringstream diagnostic;
        diagnostic << "chunk=" << chunk_index << " size=" << left->size
                   << " session_uid=" << left->id_session_uid
                   << " byte_in_chunk=" << first_difference
                   << " byte_in_stream=" << stream_offset + first_difference
                   << " left=" << int(static_cast<unsigned char>(left->buf[first_difference]))
                   << " right=" << int(static_cast<unsigned char>(right->buf[first_difference]));
        std::string stream;
        for (const MemFileChunk *chunk = first_chunk(a); chunk;
             chunk = static_cast<const MemFileChunk *>(chunk->next))
        {
          stream.append(chunk->buf, size_t(chunk->size));
        }
        auto close = [](FileReader *reader) { reader->close(reader); };
        std::unique_ptr<FileReader, decltype(close)> reader(
            BLI_filereader_new_memory(stream.data(), stream.size()), close);
        const auto decoded = BLO_readfile_blender_header_decode(reader.get());
        if (std::holds_alternative<BlenderHeader>(decoded)) {
          const auto header = std::get<BlenderHeader>(decoded);
          while (size_t(reader->offset) < stream.size()) {
            const size_t block_start = size_t(reader->offset);
            const auto block = BLO_readfile_read_bhead(reader.get(), header.bhead_type());
            if (!block || block->len < 0 || size_t(block->len) > stream.size() - size_t(reader->offset)) {
              break;
            }
            const size_t payload_start = size_t(reader->offset);
            const size_t mismatch = stream_offset + first_difference;
            if (mismatch >= block_start && mismatch < payload_start + size_t(block->len)) {
              diagnostic << " block_code=" << block->code << " sdna=" << block->SDNAnr
                         << " payload_offset=" << int64_t(mismatch) - int64_t(payload_start);
              break;
            }
            if (block->code == BLO_CODE_ENDB ||
                reader->seek(reader.get(), block->len, SEEK_CUR) < 0)
            {
              break;
            }
          }
        }
        SCOPED_TRACE(diagnostic.str());
        EXPECT_EQ(comparison, 0);
      }
      stream_offset += size_t(left->size);
      chunk_index++;
      left = static_cast<const MemFileChunk *>(left->next);
      right = static_cast<const MemFileChunk *>(right->next);
    }
    EXPECT_EQ(left, nullptr);
    EXPECT_EQ(right, nullptr);
    EXPECT_EQ(a.size, b.size);
    EXPECT_EQ(ignored_bytes, global_a->second);
  }

  wmWindow *add_window()
  {
    WorkSpace *workspace = BKE_workspace_add(main_, "WitnessWorkspace");
    bScreen *screen = BKE_id_new<bScreen>(main_, "WitnessScreen");
    BKE_workspace_layout_add(main_, *workspace, *screen, "WitnessLayout");
    wmWindowManager *manager = BKE_id_new<wmWindowManager>(main_, "WitnessWM");
    manager->runtime = MEM_new<bke::WindowManagerRuntime>(__func__);
    wmWindow *window = wm_window_new(main_, manager, nullptr, false);
    BKE_workspace_active_set(window->workspace_hook, workspace);
    window->scene = scene_;
    const ViewLayer &layer = *static_cast<const ViewLayer *>(scene_->view_layers.first);
    STRNCPY(window->view_layer_name, layer.name);
    return window;
  }

  static void expect_exact_witness(const MemFile &a, const MemFile &b)
  {
    const MemFileChunk *left = first_chunk(a);
    const MemFileChunk *right = first_chunk(b);
    size_t offset = 0;
    while (left && right) {
      ASSERT_EQ(left->size, right->size);
      EXPECT_EQ(left->id_session_uid, right->id_session_uid);
      EXPECT_EQ(std::memcmp(left->buf, right->buf, size_t(left->size)), 0)
          << "MemFile stream offset " << offset;
      offset += size_t(left->size);
      left = static_cast<const MemFileChunk *>(left->next);
      right = static_cast<const MemFileChunk *>(right->next);
    }
    EXPECT_EQ(left, nullptr);
    EXPECT_EQ(right, nullptr);
    EXPECT_EQ(a.size, b.size);
    const auto *left_sharing = a.shared_storage;
    const auto *right_sharing = b.shared_storage;
    ASSERT_EQ(bool(left_sharing), bool(right_sharing));
    if (left_sharing) {
      ASSERT_EQ(left_sharing->sharing_info_by_address_id.size(),
                right_sharing->sharing_info_by_address_id.size());
      for (const auto &item : left_sharing->sharing_info_by_address_id.items()) {
        const auto *other = right_sharing->sharing_info_by_address_id.lookup_ptr(item.key);
        ASSERT_NE(other, nullptr);
        EXPECT_EQ(item.value.sharing_info, other->sharing_info);
        EXPECT_EQ(item.value.data, other->data);
      }
    }
  }
};

TEST_F(WritefileMemBudgetTest, private_witness_metadata_is_exact_across_live_call_stacks)
{
  wmWindow *window = add_window();
  ASSERT_EQ(window->scene, scene_);
  Scene *second = BKE_scene_add(main_, "BackgroundWitness");
  second->r.scemode |= R_BG_RENDER;
  /* Settle ordinary writer bookkeeping before observing an unchanged Main. The production
   * safety caller separately saves/restores recalc fields around each witness. */
  OwnedMemFile warmup, direct, nested;
  ASSERT_EQ(write_witness_at_depth(main_, &warmup.file, 0), BlendMemFileWriteResult::Success);
  ASSERT_EQ(write_witness_at_depth(main_, &direct.file, 0), BlendMemFileWriteResult::Success);
  ASSERT_EQ(write_witness_at_depth(main_, &nested.file, 7), BlendMemFileWriteResult::Success);
  expect_exact_witness(direct.file, nested.file);

  const std::string bytes = memfile_bytes(direct.file);
  auto close = [](FileReader *reader) { reader->close(reader); };
  std::unique_ptr<FileReader, decltype(close)> reader(
      BLI_filereader_new_memory(bytes.data(), bytes.size()), close);
  const auto decoded = BLO_readfile_blender_header_decode(reader.get());
  ASSERT_TRUE(std::holds_alternative<BlenderHeader>(decoded));
  const BlenderHeader header = std::get<BlenderHeader>(decoded);
  Set<const void *> metadata_addresses;
  int render_blocks = 0, global_blocks = 0;
  bool complete = false;
  while (size_t(reader->offset) < bytes.size()) {
    const auto block = BLO_readfile_read_bhead(reader.get(), header.bhead_type());
    ASSERT_TRUE(block.has_value());
    ASSERT_GE(block->len, 0);
    ASSERT_LE(size_t(block->len), bytes.size() - size_t(reader->offset));
    if (block->code == BLO_CODE_REND || block->code == BLO_CODE_GLOB) {
      ASSERT_NE(block->old, nullptr);
      EXPECT_TRUE(metadata_addresses.add(block->old));
      if (block->code == BLO_CODE_REND) {
        render_blocks++;
      }
      else {
        global_blocks++;
        ASSERT_EQ(block->len, sizeof(FileGlobal));
        FileGlobal global;
        ASSERT_EQ(reader->read(reader.get(), &global, sizeof(global)), int64_t(sizeof(global)));
        EXPECT_EQ(global.curscene, scene_);
        EXPECT_EQ(global.cur_view_layer, scene_->view_layers.first);
        continue;
      }
    }
    if (block->code == BLO_CODE_ENDB) {
      complete = true;
      break;
    }
    ASSERT_GE(reader->seek(reader.get(), block->len, SEEK_CUR), 0);
  }
  EXPECT_TRUE(complete);
  EXPECT_EQ(render_blocks, 2);
  EXPECT_EQ(global_blocks, 1);
  EXPECT_EQ(metadata_addresses.size(), 3);
}

TEST_F(WritefileMemBudgetTest, private_witness_keeps_real_payload_and_current_owner_changes)
{
  wmWindow *window = add_window();
  Scene *second = BKE_scene_add(main_, "OtherWitness");
  OwnedMemFile warmup, before, payload_changed, owner_changed, restored;
  ASSERT_EQ(write_witness_at_depth(main_, &warmup.file, 0), BlendMemFileWriteResult::Success);
  ASSERT_EQ(write_witness_at_depth(main_, &before.file, 0), BlendMemFileWriteResult::Success);
  const int previous_end = scene_->r.efra;
  scene_->r.efra++;
  ASSERT_EQ(write_witness_at_depth(main_, &payload_changed.file, 3),
            BlendMemFileWriteResult::Success);
  EXPECT_NE(memfile_bytes(before.file), memfile_bytes(payload_changed.file));
  scene_->r.efra = previous_end;
  window->scene = second;
  ASSERT_EQ(write_witness_at_depth(main_, &owner_changed.file, 5),
            BlendMemFileWriteResult::Success);
  EXPECT_NE(memfile_bytes(before.file), memfile_bytes(owner_changed.file));
  window->scene = scene_;
  ASSERT_EQ(write_witness_at_depth(main_, &restored.file, 7), BlendMemFileWriteResult::Success);
  expect_exact_witness(before.file, restored.file);
}

TEST_F(WritefileMemBudgetTest, retained_ui_phase_keeps_ui_edits_but_rejects_replaced_content)
{
  wmWindow *window = add_window();
  bScreen *screen = BKE_workspace_active_screen_get(window->workspace_hook);
  ASSERT_NE(screen, nullptr);
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  BLI_SCOPED_DEFER([&] { BKE_reports_free(&reports); });
  const int previous_flags = G.fileflags;
  BLI_SCOPED_DEFER([&] { G.fileflags = previous_flags; });
  G.fileflags &= ~G_FILE_NO_UI;
  std::unique_ptr<WMCheckpointContent, decltype(&WM_checkpoint_content_free)> content(
      WM_checkpoint_content_capture(*main_, &reports), WM_checkpoint_content_free);
  ASSERT_NE(content, nullptr);
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports),
            WMCheckpointContentMatch::Match);
  /* The broad phase is never available to a read that can replace UI. */
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports,
                                         WMCheckpointContentScope::RetainedUI),
            WMCheckpointContentMatch::Different);
  G.fileflags |= G_FILE_NO_UI;
  screen->winid = 27;
  ASSERT_TRUE(IDP_AddToGroup(IDP_EnsureProperties(&screen->id),
                            IDP_NewString("keep actual callback edit", "retained_edit")));
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports),
            WMCheckpointContentMatch::Different);
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports,
                                         WMCheckpointContentScope::RetainedUI),
            WMCheckpointContentMatch::Match);
  EXPECT_EQ(screen->winid, 27);
  EXPECT_STREQ(IDP_string_get(IDP_GetPropertyFromGroup(screen->id.properties, "retained_edit")),
               "keep actual callback edit");
  const int old_end = scene_->r.efra;
  scene_->r.efra++;
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports,
                                         WMCheckpointContentScope::RetainedUI),
            WMCheckpointContentMatch::Different);
  EXPECT_EQ(scene_->r.efra, old_end + 1);
}

TEST_F(WritefileMemBudgetTest, retained_ui_requires_exact_snapshot_membership_and_identity)
{
  wmWindow *window = add_window();
  bScreen *screen = BKE_workspace_active_screen_get(window->workspace_hook);
  ASSERT_NE(screen, nullptr);
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  BLI_SCOPED_DEFER([&] { BKE_reports_free(&reports); });
  const int previous_flags = G.fileflags;
  BLI_SCOPED_DEFER([&] { G.fileflags = previous_flags; });
  G.fileflags |= G_FILE_NO_UI;
  std::unique_ptr<WMCheckpointContent, decltype(&WM_checkpoint_content_free)> content(
      WM_checkpoint_content_capture(*main_, &reports), WM_checkpoint_content_free);
  ASSERT_NE(content, nullptr);
  const uint uid = screen->id.session_uid;
  screen->id.session_uid = 0;
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports,
                                         WMCheckpointContentScope::RetainedUI),
            WMCheckpointContentMatch::Different);
  screen->id.session_uid = uid;
  BLI_remlink(&main_->screens, screen);
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports,
                                         WMCheckpointContentScope::RetainedUI),
            WMCheckpointContentMatch::Different);
  BLI_addtail(&main_->screens, screen);
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports,
                                         WMCheckpointContentScope::RetainedUI),
            WMCheckpointContentMatch::Match);
  BKE_id_new<bScreen>(main_, "NotInSafetySnapshot");
  EXPECT_EQ(WM_checkpoint_content_matches(*main_, *content, &reports,
                                         WMCheckpointContentScope::RetainedUI),
            WMCheckpointContentMatch::Different);
}

TEST_F(WritefileMemBudgetTest, bounded_and_ordinary_bytes_match_and_exact_limit_succeeds)
{
  OwnedMemFile ordinary, bounded, exact;
  ASSERT_TRUE(BLO_write_file_mem(main_, nullptr, &ordinary.file, 0));
  size_t charged = 0;
  ASSERT_EQ(BLO_write_file_mem_bounded(main_, &bounded.file, 0, 256 * 1024 * 1024, &charged),
            BlendMemFileWriteResult::Success);
  EXPECT_GT(charged, bounded.file.size);
  expect_same(ordinary.file, bounded.file);
  size_t exact_charge = 0;
  ASSERT_EQ(BLO_write_file_mem_bounded(main_, &exact.file, 0, charged, &exact_charge),
            BlendMemFileWriteResult::Success);
  EXPECT_EQ(charged, exact_charge);
  expect_same(bounded.file, exact.file);
}

TEST_F(WritefileMemBudgetTest, one_byte_short_cleans_partial_output_and_retry_is_reusable)
{
  OwnedMemFile measured, output;
  size_t charged = 0;
  ASSERT_EQ(BLO_write_file_mem_bounded(main_, &measured.file, 0, 256 * 1024 * 1024, &charged),
            BlendMemFileWriteResult::Success);
  ASSERT_GT(charged, 0u);
  size_t refused_charge = 0;
  EXPECT_EQ(BLO_write_file_mem_bounded(main_, &output.file, 0, charged - 1, &refused_charge),
            BlendMemFileWriteResult::BudgetExceeded);
  EXPECT_LE(refused_charge, charged - 1);
  EXPECT_TRUE(output.file.chunks.is_empty());
  EXPECT_EQ(output.file.shared_storage, nullptr);
  EXPECT_EQ(output.file.size, 0u);
  EXPECT_EQ(BLO_write_file_mem_bounded(main_, &output.file, 0, charged),
            BlendMemFileWriteResult::Success);
  expect_same(measured.file, output.file);
}

TEST_F(WritefileMemBudgetTest, zero_budget_refuses_before_source_recalc_and_rejects_nonempty_target)
{
  OwnedMemFile output;
  scene_->id.recalc_up_to_undo_push = 123;
  scene_->id.recalc_after_undo_push = 456;
  size_t charged = 999;
  EXPECT_EQ(BLO_write_file_mem_bounded(main_, &output.file, 0, 0, &charged),
            BlendMemFileWriteResult::BudgetExceeded);
  EXPECT_EQ(charged, 0u);
  EXPECT_EQ(scene_->id.recalc_up_to_undo_push, 123u);
  EXPECT_EQ(scene_->id.recalc_after_undo_push, 456u);
  EXPECT_TRUE(output.file.chunks.is_empty());

  const char payload[] = "preserve invalid destination";
  MemFileWriteData data{};
  data.written_memfile = &output.file;
  BLO_memfile_chunk_add(&data, payload, sizeof(payload));
  const MemFileChunk *chunk = first_chunk(output.file);
  EXPECT_EQ(BLO_write_file_mem_bounded(main_, &output.file, 0, SIZE_MAX),
            BlendMemFileWriteResult::InvalidArguments);
  EXPECT_EQ(output.file.chunks.first, chunk);
  EXPECT_EQ(output.file.size, sizeof(payload));
  EXPECT_EQ(std::memcmp(chunk->buf, payload, sizeof(payload)), 0);
}

}  // namespace
}  // namespace blender
