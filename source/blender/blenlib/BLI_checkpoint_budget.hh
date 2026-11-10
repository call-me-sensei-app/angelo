/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bli
 *
 * A count of checkpoint refusals caused by a memory budget: a check that compared what an
 * operation needs with a budget handed down from the checkpoint memory policy
 * (#WM_checkpoint_memory_limits) and found it too small, where a larger budget would have passed
 * that check. Every budget comparison of the checkpoint paths (the policy itself, the Sculpt
 * snapshot and codec charges, the BMesh section codec, the BASE read's data budget, the cold
 * install's witnesses) counts its refusals here; failures for any other reason (I/O, a changed
 * owner, a malformed structure) never do. A Restore compares the count across its safety capture
 * and across its target's preparation to tell a memory refusal, which the other order of the two
 * might decide differently, from any other (doc/angelo/checkpoint_policy.rst, "Restore's safety
 * capture and its target"). The count only grows. It is per thread: every counted check in those
 * two windows runs on the main thread, so another thread's refusal never enters a Restore's count,
 * and #CheckpointBudgetCountPaused excludes code a window calls that is not its own (an add-on's
 * save handler).
 */

#include <cstdint>

namespace blender {

inline thread_local uint64_t checkpoint_budget_refusal_count = 0;
inline thread_local int checkpoint_budget_count_paused = 0;

/** Count one refusal by a budget comparison (on this thread, unless paused). */
inline void checkpoint_budget_refused()
{
  if (!checkpoint_budget_count_paused) {
    checkpoint_budget_refusal_count++;
  }
}

/** The number of budget refusals counted on this thread so far. */
inline uint64_t checkpoint_budget_refusals()
{
  return checkpoint_budget_refusal_count;
}

/** ``false`` (after counting a budget refusal) when ``needed`` exceeds ``budget``. */
inline bool checkpoint_budget_fits(const uint64_t needed, const uint64_t budget)
{
  if (needed > budget) {
    checkpoint_budget_refused();
    return false;
  }
  return true;
}

/** Scoped: refusals on this thread are not counted (code a counted window calls but does not own). */
class CheckpointBudgetCountPaused {
 public:
  CheckpointBudgetCountPaused()
  {
    checkpoint_budget_count_paused++;
  }
  ~CheckpointBudgetCountPaused()
  {
    checkpoint_budget_count_paused--;
  }
  CheckpointBudgetCountPaused(const CheckpointBudgetCountPaused &) = delete;
  CheckpointBudgetCountPaused &operator=(const CheckpointBudgetCountPaused &) = delete;
};

}  // namespace blender
