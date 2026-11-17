/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup gpu
 */

#pragma once

#include <cstdint>

namespace blender {

namespace gpu {
class Shader;
}

/**
 * Compile-ahead draws (Angelo): a draw whose pixels are never used, made only so that the shaders and pipeline
 * states later draws of the same scene need are prepared off the drawing thread (`view_warm.py`, the agent's views).
 *
 * Between #GPU_compile_ahead_begin and #GPU_compile_ahead_end, on the calling thread only:
 * - a backend that builds a pipeline state on a shader's first draw with a new state (Metal) queues that build to
 *   its background threads and skips that draw call, instead of building it on the calling thread;
 * - engines may queue to the background compilation what they would otherwise compile synchronously (EEVEE's
 *   world shaders), drawing a stand-in for that draw.
 * Pipeline states a later draw finds built are used as if that draw had built them. Other threads and draws are
 * unchanged; a draw that meets a world shader a compile-ahead draw queued waits for that compile instead of
 * compiling it. Not nested.
 */
void GPU_compile_ahead_begin();
void GPU_compile_ahead_end();
bool GPU_compile_ahead_is_active();

namespace gpu {
/**
 * Draws a backend makes for itself (a texture update or a blit drawn with a shader) write textures that outlive the
 * compile-ahead draw, so they are never skipped: while one of these is alive on this thread, compile-ahead is off.
 */
class CompileAheadSuspend {
  bool was_active_;

 public:
  CompileAheadSuspend();
  ~CompileAheadSuspend();
};
}  // namespace gpu

/**
 * Build in the background the pipeline states \a shader would need where \a parent has drawn: the descriptors
 * \a parent built are \a shader's too when both draw the same vertex formats into the same attachments (a
 * material's optimized variant and its unoptimized pass). True once every one is built or has failed; until then,
 * calls queue what is still missing and return false. Backends that build no pipeline states return true.
 * Upstream's parent-shader warming (#GPU_shader_set_parent, #GPU_shader_warm_cache) did this synchronously until
 * the GPUMaterial refactor e6638d6e5eb dropped its callers. Thread safe.
 */
bool GPU_shader_warm_cache_async(gpu::Shader *shader, gpu::Shader *parent);

/** Counts of the graphics pipeline states (PSOs) built since start-up, for diagnostics and tests. */
struct GPUPipelineStats {
  /** Built inside a draw call, which waits for it (any thread: a render job's draws count too): the stall
   * compile-ahead draws avoid. */
  uint64_t built_in_draw = 0;
  double built_in_draw_ms = 0.0;
  /** Built outside any draw: queued by compile-ahead draws or #GPU_shader_warm_cache_async, or a backend's own
   * precompilation from a shader's declared pipeline states (Vulkan). */
  uint64_t built_in_background = 0;
  double built_in_background_ms = 0.0;
  /** Queued and not built yet (building, or waiting for a thread). */
  int64_t pending = 0;
  /** Builds queued since start-up, built or not: the number of the latest (they are numbered from 1 as they are
   * queued, #GPU_pipeline_builds_pending). */
  uint64_t queued = 0;
  /** Queued builds that failed (the draw that needs one builds it again, as without compile-ahead). */
  uint64_t failed = 0;
  /** Material shaders (GPU passes) a thread compiled itself, or waited for, where it asked for them, instead of
   * leaving them to the background compilation: EEVEE asks so for a world's in every draw but a compile-ahead one. */
  uint64_t shaders_waited = 0;
};
GPUPipelineStats GPU_pipeline_stats_get();

/**
 * How many of the builds numbered above \a after up to \a through are not built yet: with #GPUPipelineStats::queued
 * read before and after a draw, those that draw queued, whatever other draws and threads queued before or since
 * (a view waits for its own first draws' builds, never for another window's or an optimized variant's).
 */
int64_t GPU_pipeline_builds_pending(uint64_t after, uint64_t through);

namespace gpu {
/** Backends record their pipeline builds here (#GPU_pipeline_stats_get). Thread safe. */
void pipeline_stats_built(bool in_draw, double milliseconds);
/** A build was queued: its number, to hand back once it is built, has failed or was dropped. */
uint64_t pipeline_build_queued();
void pipeline_build_finished(uint64_t number);
void pipeline_stats_failed();
/** A material shader was compiled, or waited for, by the thread that asked for it (#GPUPipelineStats). */
void pipeline_stats_shader_waited();
}  // namespace gpu

}  // namespace blender
