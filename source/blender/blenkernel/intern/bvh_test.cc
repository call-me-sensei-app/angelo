/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include "BKE_bvh.hh"

#ifdef WITH_EMBREE
#  include <embree4/rtcore.h>
#endif

namespace blender::bke::bvh::tests {

#ifdef WITH_EMBREE

/**
 * Embree rejects a call with a null handle by throwing and catching a C++ exception inside the
 * call, then records the error for the calling thread, which this reads and clears. A tree that was
 * never built (default-constructed or moved from) holds no handles, and every Mesh copy destroys
 * such trees, so the unwinding made each copy several times slower on Blender 5.3.
 */
static RTCError take_thread_error()
{
  return rtcGetDeviceError(nullptr);
}

TEST(bvh_tree, empty_tree_destruction_makes_no_embree_call)
{
  take_thread_error();
  {
    Tree tree;
  }
  EXPECT_EQ(take_thread_error(), RTC_ERROR_NONE);
}

TEST(bvh_tree, moved_from_tree_makes_no_embree_call)
{
  take_thread_error();
  {
    Tree source;
    Tree moved(std::move(source));
    Tree assigned;
    assigned = std::move(moved);
  }
  EXPECT_EQ(take_thread_error(), RTC_ERROR_NONE);
}

TEST(bvh_tree, empty_tree_queries_find_nothing_without_embree_calls)
{
  const Tree tree;
  take_thread_error();
  const Ray ray(float3(0.0f, 0.0f, -1.0f), float3(0.0f, 0.0f, 1.0f));
  EXPECT_FALSE(tree.ray_intersect(ray).has_value());
  int hits = 0;
  tree.ray_intersect_all(ray, [&](const RayHit & /*hit*/) { hits++; });
  EXPECT_FALSE(tree.closest_point(float3(0.0f), 10.0f).has_value());
  tree.range_query(float3(0.0f), 10.0f, [&](const int /*index*/) {
    hits++;
    return true;
  });
  EXPECT_EQ(hits, 0);
  EXPECT_EQ(take_thread_error(), RTC_ERROR_NONE);
}

#endif /* WITH_EMBREE */

}  // namespace blender::bke::bvh::tests
