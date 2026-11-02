/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bke
 *
 * Angelo: what the latest render job reported, kept after the job ends.
 *
 * A render job's progress lives in its `wmJob`, which Blender frees when the job ends. Python reads it from the
 * main thread (`_bpy._angelo_jobs.progress`) only when it polls, so samples that finished after its last poll were
 * never seen: a RENDERED look stopped then dropped the partial picture Blender had written, and could not say when
 * its first sample came (`view_render.Runner.finish`). The render job's own thread records here every progress it
 * reports and when progress first became nonzero (its first sample); Python reads the record once the job has
 * ended (`_bpy._angelo_jobs.last_progress`).
 *
 * One record, for the latest render job, keyed by the scene that owns it (Blender runs one render job per scene).
 * The job's start resets it on the main thread (`job_progress_started`); the job's thread writes it
 * (`job_progress_report`); everything is atomic.
 */

#include <atomic>
#include <cstdint>

#include "BKE_angelo_job_deadline.hh"

namespace blender::bke::angelo {

/** The owning scene's `ID.session_uid` of the render job the record describes; 0 before any. */
inline std::atomic<uint32_t> job_progress_scene{0};
/** The highest progress (0 to 1) the job reported. */
inline std::atomic<float> job_progress_last{0.0f};
/** Steady-clock nanoseconds (`job_deadline_now_ns`) when the job first reported progress above 0; 0 until then. */
inline std::atomic<int64_t> job_progress_first_ns{0};

/** A render job of the scene `scene_uid` starts: the record is now its own, with nothing reported. */
inline void job_progress_started(const uint32_t scene_uid)
{
  job_progress_scene.store(0, std::memory_order_release);
  job_progress_last.store(0.0f, std::memory_order_relaxed);
  job_progress_first_ns.store(0, std::memory_order_relaxed);
  job_progress_scene.store(scene_uid, std::memory_order_release);
}

/** The render job of the scene `scene_uid` reports `progress` (any thread). */
inline void job_progress_report(const uint32_t scene_uid, const float progress)
{
  if (job_progress_scene.load(std::memory_order_acquire) != scene_uid || !(progress > 0.0f)) {
    return;
  }
  float last = job_progress_last.load(std::memory_order_relaxed);
  while (progress > last &&
         !job_progress_last.compare_exchange_weak(last, progress, std::memory_order_relaxed))
  {
  }
  int64_t none = 0;
  job_progress_first_ns.compare_exchange_strong(none, job_deadline_now_ns(), std::memory_order_relaxed);
}

}  // namespace blender::bke::angelo
