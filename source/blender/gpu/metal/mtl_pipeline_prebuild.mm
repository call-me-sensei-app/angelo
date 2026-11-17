/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup gpu
 */

#include "GPU_capabilities.hh"
#include "GPU_compile_ahead.hh"

#include <algorithm>
#include <pthread.h>

#include "mtl_pipeline_prebuild.hh"
#include "mtl_shader.hh"

namespace blender::gpu {

MTLPipelinePrebuilder::MTLPipelinePrebuilder(int threads_count) : threads_count_(threads_count) {}

MTLPipelinePrebuilder::~MTLPipelinePrebuilder()
{
  {
    std::lock_guard lock(mutex_);
    stopping_ = true;
  }
  work_cv_.notify_all();
  for (std::thread &thread : threads_) {
    thread.join();
  }
  /* What no thread took is dropped: the draw that needs it builds it, as without compile-ahead draws. */
  for (Job &job : queue_) {
    [job.device release];
    pipeline_build_finished(job.number);
  }
  queue_.clear();
}

void MTLPipelinePrebuilder::push(MTLShader *shader,
                                 id<MTLDevice> device,
                                 MTLPrimitiveTopologyClass prim_type,
                                 const MTLRenderPipelineStateDescriptor &descriptor)
{
  {
    std::lock_guard lock(mutex_);
    if (threads_.is_empty()) {
      /* Each build mostly waits for Metal's compiler service, which runs several at once; shader compilation has
       * its own threads (#MTLShaderCompiler). 0: half of those, at most 4. */
      if (threads_count_ <= 0) {
        threads_count_ = std::clamp(GPU_max_parallel_compilations() / 2, 1, 4);
      }
      for (int i = 0; i < threads_count_; i++) {
        threads_.append(std::thread([this]() { this->run(); }));
      }
    }
    [device retain];
    queue_.push_back({shader, device, prim_type, descriptor, pipeline_build_queued()});
  }
  work_cv_.notify_one();
}

void MTLPipelinePrebuilder::forget(MTLShader *shader)
{
  std::unique_lock lock(mutex_);
  for (auto it = queue_.begin(); it != queue_.end();) {
    if (it->shader == shader) {
      [it->device release];
      pipeline_build_finished(it->number);
      it = queue_.erase(it);
    }
    else {
      ++it;
    }
  }
  done_cv_.wait(lock, [&]() { return !running_.contains(shader); });
}

void MTLPipelinePrebuilder::run()
{
  pthread_setname_np("MTLPipelinePrebuild");
  while (true) {
    Job job;
    {
      std::unique_lock lock(mutex_);
      work_cv_.wait(lock, [&]() { return stopping_ || !queue_.empty(); });
      if (stopping_) {
        return;
      }
      job = queue_.front();
      queue_.pop_front();
      running_.append(job.shader);
    }
    @autoreleasepool {
      job.shader->prebuild_graphic_pipeline_state(job.device, job.prim_type, job.descriptor);
    }
    [job.device release];
    {
      std::lock_guard lock(mutex_);
      running_.remove_first_occurrence_and_reorder(job.shader);
    }
    /* After the state is in the shader's cache: a reader that sees nothing pending finds it built. */
    pipeline_build_finished(job.number);
    done_cv_.notify_all();
  }
}

}  // namespace blender::gpu
