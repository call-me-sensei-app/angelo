/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup gpu
 *
 * The buffer pool's sweep for stale allocations (#MTLBufferPool::update_memory_pools).
 *
 * The window manager calls #update_memory_pools on every pass of its main loop, about 160 times a
 * second while nothing happens, and the sweep visits every pooled buffer. What it frees depends
 * only on the clock's second and on what the pools hold, so it must not run again within a second
 * unless buffers were added. Needs a Metal device, no window and no GPU context.
 */

#include "testing/testing.h"

#include "mtl_context.hh"
#include "mtl_memory.hh"

#include <chrono>
#include <thread>

namespace blender::gpu::tests {

/* Allocate and free `count` small buffers and let their safe-free list complete: the next
 * #update_memory_pools adds them to the pools. */
static void free_buffers_towards_the_pools(MTLBufferPool &pool, const int count)
{
  Vector<gpu::MTLBuffer *> buffers;
  for (int i = 0; i < count; i++) {
    buffers.append(pool.allocate(256, true));
  }
  for (gpu::MTLBuffer *buffer : buffers) {
    buffer->free();
  }
  /* A list completes once it is two lists old (#MTLBufferPool::begin_new_safe_list). */
  pool.begin_new_safe_list();
  pool.begin_new_safe_list();
}

TEST(MTLBufferPoolTest, stale_sweep_runs_once_a_second_unless_buffers_were_added)
{
  id<MTLDevice> device = MTLCreateSystemDefaultDevice();
  if (device == nil) {
    GTEST_SKIP() << "No Metal device on this machine";
  }
  MTLContext::global_memory_manager_acquire_ref();
  MTLBufferPool &pool = *MTLContext::get_global_memory_manager();
  pool.init(device);

  free_buffers_towards_the_pools(pool, 64);
  EXPECT_TRUE(pool.has_completed_safe_lists());
  pool.update_memory_pools();
  EXPECT_FALSE(pool.has_completed_safe_lists());

  /* The main loop's idle passes: nothing added, the same second (or the next, once). */
  const uint64_t before = pool.stale_sweep_count();
  for (int pass = 0; pass < 200; pass++) {
    pool.update_memory_pools();
  }
  const uint64_t idle_sweeps = pool.stale_sweep_count() - before;
  EXPECT_LE(idle_sweeps, 2) << "200 passes with nothing added swept the pools " << idle_sweeps
                            << " times";

  /* Buffers added in the same second are swept at once: the pools' size decides what is stale. */
  free_buffers_towards_the_pools(pool, 8);
  const uint64_t before_added = pool.stale_sweep_count();
  pool.update_memory_pools();
  EXPECT_EQ(pool.stale_sweep_count(), before_added + 1);

  /* A new second is swept, with nothing added. */
  const uint64_t before_second = pool.stale_sweep_count();
  std::this_thread::sleep_for(std::chrono::milliseconds(1100));
  pool.update_memory_pools();
  EXPECT_EQ(pool.stale_sweep_count(), before_second + 1);

  MTLContext::global_memory_manager_release_ref();
  [device release];
}

}  // namespace blender::gpu::tests
