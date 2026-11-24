/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 *
 * Parallel loops for the retopology core with the standard library only. Every loop body writes
 * only its own indices, so results do not depend on the thread count or scheduling (the core is
 * deterministic).
 */

#pragma once

#include <algorithm>
#include <cstdint>
#include <thread>
#include <vector>

namespace blender::geometry::retopo {

/** Call `fn(i)` for `i` in `[0, size)` on all cores, in chunks of at least `grain`. */
template<typename Fn> void parallel_for(const int64_t size, const int64_t grain, const Fn &fn)
{
  if (size <= 0) {
    return;
  }
  const int64_t hw = std::max<int64_t>(1, int64_t(std::thread::hardware_concurrency()));
  const int64_t chunks = std::min<int64_t>(
      hw, std::max<int64_t>(1, size / std::max<int64_t>(grain, 1)));
  if (chunks <= 1) {
    for (int64_t i = 0; i < size; i++) {
      fn(i);
    }
    return;
  }
  std::vector<std::thread> threads;
  threads.reserve(size_t(chunks));
  for (int64_t c = 0; c < chunks; c++) {
    const int64_t begin = size * c / chunks, end = size * (c + 1) / chunks;
    threads.emplace_back([begin, end, &fn]() {
      for (int64_t i = begin; i < end; i++) {
        fn(i);
      }
    });
  }
  for (std::thread &t : threads) {
    t.join();
  }
}

}  // namespace blender::geometry::retopo
