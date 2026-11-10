/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <cstdint>
#include <limits>
#include <optional>
#include <thread>

#include "MEM_guardedalloc.h"

#include "BKE_report.hh"
#include "BLI_checkpoint_budget.hh"
#include "BLI_threads.h"

#include "wm_checkpoint.hh"

namespace blender::tests {

/** Seams set in one test never leak into the next. */
class wm_checkpoint_memory : public ::testing::Test {
 protected:
  ReportList reports_;

  void SetUp() override
  {
    BLI_threadapi_init();
    /* Report storage belongs to the fixture before the allocator-aware RAM seam is set. */
    BKE_reports_init(&reports_, RPT_STORE);
  }
  void TearDown() override
  {
    WM_checkpoint_test_available_memory(std::nullopt);
    BKE_reports_free(&reports_);
  }

  /** The policy's verdict for ``base_bytes`` against the RAM it reads now. */
  bool admitted(const uint64_t base_bytes, size_t *r_backing = nullptr, size_t *r_codec = nullptr)
  {
    BKE_reports_clear(&reports_);
    ed::sculpt_paint::checkpoint::CodecLimits limits{};
    const bool result = WM_checkpoint_memory_limits(limits, base_bytes, &reports_, r_backing);
    if (r_codec) {
      *r_codec = limits.max_backing_bytes;
    }
    return result;
  }
};

TEST_F(wm_checkpoint_memory, offsets_add_and_clamp)
{
  constexpr int64_t most = std::numeric_limits<int64_t>::max();
  constexpr int64_t least = std::numeric_limits<int64_t>::min();
  EXPECT_EQ(WM_checkpoint_memory_offset_add(2, 3), 5);
  EXPECT_EQ(WM_checkpoint_memory_offset_add(most, 1), most);
  EXPECT_EQ(WM_checkpoint_memory_offset_add(least, -1), least);
  EXPECT_EQ(WM_checkpoint_memory_offset_add(most, least), -1);
  EXPECT_EQ(WM_checkpoint_memory_offset_of(7), 7);
  EXPECT_EQ(WM_checkpoint_memory_offset_of(std::numeric_limits<uint64_t>::max()), most);
}

/** The policy reads the available RAM shifted by the offset in scope: half is reserved, BASE gets a
 * quarter of the rest, each codec budget an eighth. Exactly at a limit admits; one byte short refuses. */
TEST_F(wm_checkpoint_memory, an_offset_shifts_every_admission_in_its_scope)
{
  const uint64_t base = uint64_t(64) << 20;
  WM_checkpoint_test_available_memory(base * 8);
  size_t backing = 0, codec = 0;
  EXPECT_TRUE(admitted(base, &backing, &codec));
  EXPECT_EQ(backing, size_t(base));
  EXPECT_EQ(codec, size_t(base / 2));
  {
    WMCheckpointMemoryOffset debit(-8);
    EXPECT_FALSE(admitted(base));   /* Eight bytes short of the BASE limit. */
    {
      WMCheckpointMemoryOffset credit(8);
      EXPECT_TRUE(admitted(base));   /* Nested offsets add. */
    }
    EXPECT_FALSE(admitted(base));   /* The inner scope restored the outer one. */
  }
  EXPECT_TRUE(admitted(base));
  {
    WMCheckpointMemoryOffset everything(std::numeric_limits<int64_t>::min());
    EXPECT_FALSE(admitted(0));   /* Clamped at no RAM: nothing to reserve. */
  }
  {
    WMCheckpointMemoryOffset beyond(std::numeric_limits<int64_t>::max());
    EXPECT_TRUE(admitted(uint64_t(1) << 58));   /* Clamped, never wrapped. */
  }
}

/** The seam's model: the available RAM falls by what Blender's allocator takes after it was set and
 * rises again when that is freed, so a footprint measured across work is what the work kept. */
TEST_F(wm_checkpoint_memory, the_test_seam_follows_guarded_allocations)
{
  const uint64_t start = uint64_t(1) << 40;
  WM_checkpoint_test_available_memory(start);
  const uint64_t before = WM_checkpoint_available_memory();
  const size_t size = size_t(16) << 20;
  void *block = MEM_new_uninitialized(size, __func__);
  const uint64_t during = WM_checkpoint_available_memory();
  EXPECT_GE(before - during, uint64_t(size));
  MEM_delete_void(block);
  EXPECT_EQ(WM_checkpoint_available_memory(), before);
  WM_checkpoint_test_available_memory(std::nullopt);
  /* Outside the seam the OS is read again (whatever it answers on this machine). */
  EXPECT_GT(WM_checkpoint_available_memory(), uint64_t(0));
}

/** A budget comparison counts only its refusal (BLI_checkpoint_budget.hh). */
TEST_F(wm_checkpoint_memory, a_budget_comparison_counts_only_its_refusal)
{
  const uint64_t before = checkpoint_budget_refusals();
  EXPECT_TRUE(checkpoint_budget_fits(8, 8));
  EXPECT_EQ(checkpoint_budget_refusals(), before);
  EXPECT_FALSE(checkpoint_budget_fits(9, 8));
  EXPECT_EQ(checkpoint_budget_refusals(), before + 1);
  EXPECT_EQ(WM_checkpoint_budget_refusals(), before + 1);
}

/** Another thread's refusal and a paused scope's are not this thread's count (L4d). */
TEST_F(wm_checkpoint_memory, only_this_thread_s_unpaused_refusals_count)
{
  const uint64_t before = checkpoint_budget_refusals();
  std::thread other([]() { checkpoint_budget_refused(); });
  other.join();
  EXPECT_EQ(checkpoint_budget_refusals(), before);
  {
    CheckpointBudgetCountPaused paused;
    EXPECT_FALSE(checkpoint_budget_fits(2, 1));
  }
  EXPECT_EQ(checkpoint_budget_refusals(), before);
  EXPECT_FALSE(checkpoint_budget_fits(2, 1));
  EXPECT_EQ(checkpoint_budget_refusals(), before + 1);
}

/** The policy's own refusal counts as a budget refusal (Restore's ``budget`` outcome); an admission does not. */
TEST_F(wm_checkpoint_memory, a_budget_refusal_is_counted)
{
  WM_checkpoint_test_available_memory(0);
  const uint64_t before = WM_checkpoint_budget_refusals();
  EXPECT_FALSE(admitted(0));
  EXPECT_EQ(WM_checkpoint_budget_refusals(), before + 1);
  WM_checkpoint_test_available_memory(uint64_t(1) << 30);
  EXPECT_TRUE(admitted(0));
  EXPECT_EQ(WM_checkpoint_budget_refusals(), before + 1);
}

/** #206's order, replayed by the offsets, on the policy alone: a safety capture admitted under the
 * debit of a target's retained bound is admitted by the earlier order whenever the target kept no
 * more than that bound (the verdict is monotone in the RAM it reads); a capture refused under the
 * debit is not decided here (Restore takes the earlier order then). The capture and the preparation
 * themselves need a document, a Python owner and files, which a GTest has not:
 * test_blender_checkpoint_safety_overlap runs them through this path near the limit. */
TEST_F(wm_checkpoint_memory, the_debit_never_admits_what_the_earlier_order_refuses)
{
  const uint64_t available = uint64_t(1) << 33;
  WM_checkpoint_test_available_memory(available);
  size_t backing = 0, codec = 0;
  ASSERT_TRUE(admitted(uint64_t(1) << 20, &backing, &codec));
  const int64_t debit = WM_checkpoint_memory_offset_add(
      WM_checkpoint_memory_offset_of(backing),
      WM_checkpoint_memory_offset_add(WM_checkpoint_memory_offset_of(codec),
                                      WM_checkpoint_memory_offset_of(codec)));
  /* The capture's own budget (codec backing, base 0) under the debit and with targets of any size
   * up to it already taken, as the earlier order read the RAM. */
  size_t under_debit = 0;
  {
    WMCheckpointMemoryOffset scope(-debit);
    ASSERT_TRUE(admitted(0, nullptr, &under_debit));
  }
  for (const int64_t kept : {int64_t(0), debit / 3, debit / 2, debit}) {
    size_t earlier = 0;
    WMCheckpointMemoryOffset target(-kept);
    ASSERT_TRUE(admitted(0, nullptr, &earlier));
    EXPECT_GE(earlier, under_debit) << "a target that kept " << kept << " bytes";
  }
}

}  // namespace blender::tests
