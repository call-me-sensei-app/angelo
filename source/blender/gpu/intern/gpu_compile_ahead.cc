/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup gpu
 */

#include "BLI_assert.hh"

#include "GPU_compile_ahead.hh"

#include "gpu_shader_private.hh"

#include <algorithm>
#include <atomic>
#include <mutex>
#include <set>

namespace blender {

/* -------------------------------------------------------------------- */
/** \name Compile-ahead draws
 * \{ */

/* Per thread: a render job's draws on its own thread are never compile-ahead draws. */
static thread_local bool g_compile_ahead = false;

void GPU_compile_ahead_begin()
{
  BLI_assert_msg(!g_compile_ahead, "Compile-ahead draws are not nested");
  g_compile_ahead = true;
}

void GPU_compile_ahead_end()
{
  g_compile_ahead = false;
}

bool GPU_compile_ahead_is_active()
{
  return g_compile_ahead;
}

gpu::CompileAheadSuspend::CompileAheadSuspend() : was_active_(g_compile_ahead)
{
  g_compile_ahead = false;
}

gpu::CompileAheadSuspend::~CompileAheadSuspend()
{
  g_compile_ahead = was_active_;
}

bool GPU_shader_warm_cache_async(gpu::Shader *shader, gpu::Shader *parent)
{
  if (shader == nullptr || parent == nullptr || shader == parent) {
    return true;
  }
  return shader->warm_cache_async(parent);
}

static struct {
  std::atomic<uint64_t> built_in_draw{0}, built_in_background{0}, failed{0}, shaders_waited{0};
  std::atomic<uint64_t> built_in_draw_us{0}, built_in_background_us{0};
} g_pipeline_stats;

/* The builds queued and not finished, by number: a handful at a time. */
static struct {
  std::mutex mutex;
  std::set<uint64_t> unfinished;
  uint64_t queued = 0;
} g_pipeline_builds;

void gpu::pipeline_stats_built(bool in_draw, double milliseconds)
{
  const uint64_t us = uint64_t(std::max(milliseconds, 0.0) * 1000.0);
  if (in_draw) {
    g_pipeline_stats.built_in_draw++;
    g_pipeline_stats.built_in_draw_us += us;
  }
  else {
    g_pipeline_stats.built_in_background++;
    g_pipeline_stats.built_in_background_us += us;
  }
}

uint64_t gpu::pipeline_build_queued()
{
  std::lock_guard lock(g_pipeline_builds.mutex);
  g_pipeline_builds.unfinished.insert(++g_pipeline_builds.queued);
  return g_pipeline_builds.queued;
}

void gpu::pipeline_build_finished(uint64_t number)
{
  std::lock_guard lock(g_pipeline_builds.mutex);
  g_pipeline_builds.unfinished.erase(number);
}

int64_t GPU_pipeline_builds_pending(uint64_t after, uint64_t through)
{
  if (through <= after) {
    return 0;
  }
  std::lock_guard lock(g_pipeline_builds.mutex);
  const std::set<uint64_t> &unfinished = g_pipeline_builds.unfinished;
  return int64_t(std::distance(unfinished.upper_bound(after), unfinished.upper_bound(through)));
}

void gpu::pipeline_stats_failed()
{
  g_pipeline_stats.failed++;
}

void gpu::pipeline_stats_shader_waited()
{
  g_pipeline_stats.shaders_waited++;
}

GPUPipelineStats GPU_pipeline_stats_get()
{
  GPUPipelineStats stats;
  stats.built_in_draw = g_pipeline_stats.built_in_draw;
  stats.built_in_draw_ms = double(g_pipeline_stats.built_in_draw_us) / 1000.0;
  stats.built_in_background = g_pipeline_stats.built_in_background;
  stats.built_in_background_ms = double(g_pipeline_stats.built_in_background_us) / 1000.0;
  {
    std::lock_guard lock(g_pipeline_builds.mutex);
    stats.pending = int64_t(g_pipeline_builds.unfinished.size());
    stats.queued = g_pipeline_builds.queued;
  }
  stats.failed = g_pipeline_stats.failed;
  stats.shaders_waited = g_pipeline_stats.shaders_waited;
  return stats;
}

/** \} */

}  // namespace blender
