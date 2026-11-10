/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include <atomic>
#include <thread>
#include <vector>

#include "bpy_angelo_checkpoint_binding.hh"

namespace blender::tests {

/** The content hasher's feeder gate: ``update`` holds it, a second feeder and ``capture_bind`` see it held. */
TEST(bpy_angelo_checkpoint_binding, one_feeder_holds_the_hasher)
{
  CheckpointHasherUse use;
  EXPECT_FALSE(use.held());
  EXPECT_TRUE(use.try_acquire());
  EXPECT_TRUE(use.held());   /* What capture_bind refuses. */
  EXPECT_FALSE(use.try_acquire());   /* A second feeder. */
  use.release();
  EXPECT_FALSE(use.held());
  EXPECT_TRUE(use.try_acquire());
  use.release();
}

TEST(bpy_angelo_checkpoint_binding, feeders_on_many_threads_never_overlap)
{
  CheckpointHasherUse use;
  std::atomic<int> inside{0}, most{0}, acquired{0};
  std::vector<std::thread> threads;
  for (int index = 0; index < 4; index++) {
    threads.emplace_back([&]() {
      for (int attempt = 0; attempt < 20000; attempt++) {
        if (!use.try_acquire()) {
          continue;
        }
        const int now = inside.fetch_add(1) + 1;
        int seen = most.load();
        while (now > seen && !most.compare_exchange_weak(seen, now)) {
        }
        acquired.fetch_add(1);
        inside.fetch_sub(1);
        use.release();
      }
    });
  }
  for (std::thread &thread : threads) {
    thread.join();
  }
  EXPECT_EQ(most.load(), 1);
  EXPECT_GT(acquired.load(), 0);
  EXPECT_FALSE(use.held());
}

}  // namespace blender::tests
