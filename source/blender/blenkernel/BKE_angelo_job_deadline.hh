/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bke
 *
 * Angelo: the time budget of the render or bake job Angelo started, enforced on the job's own thread.
 *
 * A job's break check (the render job's `render_breakjob`, the bake's `bake_break`) is what every
 * renderer polls, from its own thread, to learn it must stop. Angelo's time budget used to be checked by a
 * main-thread timer; when the main thread was slow (Metal presentation paced on a starved GPU) the budget
 * fired hours late. The deadline here is read by those break checks, so the job stops itself when its
 * budget runs out, whatever the main thread is doing. Contract: doc/angelo/long_jobs.rst, "Time budget".
 *
 * The deadline belongs to one job: the kind of job (its `WM_JOB_TYPE_*`) and the scene that owns it. Only
 * that job's break check reads it, so a render or bake the person starts meanwhile, of another scene or of
 * another kind, is never stopped by the agent's budget, and the job's own end clears it
 * (`job_deadline_job_ended`), so a deadline can never outlive its job. Blender itself runs one render job
 * per scene and one bake job per scene (`WM_jobs_test`), so kind and scene name the job exactly.
 *
 * `job_deadline_fired` says whether a break check returned true because of the deadline: the job was
 * stopped by its budget, as opposed to finishing just before or after it. It stays set until the next
 * deadline is set or cleared (`job_deadline_set`), so it can be read once the job has ended.
 *
 * Clearing is scoped too (`job_deadline_job_ended`): Python clears only the deadline of the job it
 * ended, so a job of a dropped manager (Reload Scripts) keeps its own until its end clears it.
 *
 * Set and cleared on the main thread (`_bpy._angelo_jobs.deadline`, and the job's end callback, which
 * Blender runs on the main thread); read from the job's thread (atomics).
 */

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>

namespace blender::bke::angelo {

/** Steady-clock nanoseconds when the job's budget ends; 0 when none is set. */
inline std::atomic<int64_t> job_deadline_ns{0};
/** The job the deadline belongs to: its `WM_JOB_TYPE_*` and its owning scene's `ID.session_uid`. */
inline std::atomic<int> job_deadline_kind{0};
inline std::atomic<uint32_t> job_deadline_scene{0};
/** True once the job's break check returned true because of the deadline. */
inline std::atomic<bool> job_deadline_fired{false};

/** Longest budget accepted: far past any job's (Python caps budgets at hours); keeps `seconds * 1e9`
 * well inside int64. */
inline constexpr double job_deadline_max_seconds = 1e7;

inline int64_t job_deadline_now_ns()
{
  return std::chrono::duration_cast<std::chrono::nanoseconds>(
             std::chrono::steady_clock::now().time_since_epoch())
      .count();
}

/** The budget of the `kind` job owned by the scene `scene_uid` ends `seconds` from now. */
inline void job_deadline_set(const double seconds, const int kind, const uint32_t scene_uid)
{
  job_deadline_ns.store(0, std::memory_order_release);
  job_deadline_fired.store(false, std::memory_order_relaxed);
  if (seconds <= 0.0) {
    return;
  }
  job_deadline_kind.store(kind, std::memory_order_relaxed);
  job_deadline_scene.store(scene_uid, std::memory_order_relaxed);
  job_deadline_ns.store(
      job_deadline_now_ns() + int64_t(std::min(seconds, job_deadline_max_seconds) * 1e9),
      std::memory_order_release);
}

inline void job_deadline_clear()
{
  job_deadline_ns.store(0, std::memory_order_release);
}

inline bool job_deadline_owns(const int kind, const uint32_t scene_uid)
{
  return job_deadline_kind.load(std::memory_order_relaxed) == kind &&
         job_deadline_scene.load(std::memory_order_relaxed) == scene_uid;
}

/** True once the deadline of this job (kind, scene) has passed; records that it fired (any thread). */
inline bool job_deadline_passed(const int kind, const uint32_t scene_uid)
{
  const int64_t deadline = job_deadline_ns.load(std::memory_order_acquire);
  if (deadline == 0 || !job_deadline_owns(kind, scene_uid) || job_deadline_now_ns() < deadline) {
    return false;
  }
  job_deadline_fired.store(true, std::memory_order_relaxed);
  return true;
}

/** The deadline of the job (kind, scene), if it is that job's, is cleared; `job_deadline_fired` is kept.
 * Called by the job's own end and free, and by Python for the job it ended. */
inline void job_deadline_job_ended(const int kind, const uint32_t scene_uid)
{
  if (job_deadline_ns.load(std::memory_order_acquire) != 0 && job_deadline_owns(kind, scene_uid)) {
    job_deadline_clear();
  }
}

}  // namespace blender::bke::angelo
