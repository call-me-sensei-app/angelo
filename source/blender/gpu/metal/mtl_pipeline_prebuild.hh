/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup gpu
 *
 * The render pipeline states compile-ahead draws ask for (#GPU_compile_ahead_begin), built on background threads.
 *
 * A Metal render pipeline state is built on a shader's first draw with a new state (vertex layout, attachment
 * formats, blending), on the drawing thread: Metal's out-of-process compiler turns the shader into the GPU's code for
 * that state while the thread waits, tens of milliseconds a state, so the first draw of an object with several new
 * materials held Blender's main loop for 330-360 ms (Mac 1, five materials). A compile-ahead draw's pixels are never
 * used, so its misses are queued here instead and that draw call is skipped; the draws that come after find the
 * states built. Builds use #MTLShader::bake_graphic_pipeline_state, written to be callable from a secondary thread.
 */

#pragma once

#include "BLI_vector.hh"

#include <Metal/Metal.h>

#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>

#include "mtl_pso_descriptor_state.hh"

namespace blender::gpu {

class MTLShader;

class MTLPipelinePrebuilder {
 private:
  struct Job {
    MTLShader *shader;
    id<MTLDevice> device;
    MTLPrimitiveTopologyClass prim_type;
    MTLRenderPipelineStateDescriptor descriptor;
    /** Its number among the builds queued (#pipeline_build_queued). */
    uint64_t number;
  };

  int threads_count_;
  Vector<std::thread> threads_;
  std::mutex mutex_;
  std::condition_variable work_cv_;
  std::condition_variable done_cv_;
  std::deque<Job> queue_;
  /** The shaders whose builds run now, once per running build. */
  Vector<MTLShader *> running_;
  bool stopping_ = false;

 public:
  /** \param threads_count: background threads, started on first use; 0 decides then. */
  explicit MTLPipelinePrebuilder(int threads_count);
  /** Drops the queued builds and waits for the running ones. */
  ~MTLPipelinePrebuilder();

  /** Queue a build of \a descriptor for \a shader, which marked it in flight. Threads start on first use. */
  void push(MTLShader *shader,
            id<MTLDevice> device,
            MTLPrimitiveTopologyClass prim_type,
            const MTLRenderPipelineStateDescriptor &descriptor);

  /** Drop \a shader's queued builds and wait for its running ones: called by the shader before it frees anything
   * a build reads. Any thread. */
  void forget(MTLShader *shader);

 private:
  void run();
};

}  // namespace blender::gpu
