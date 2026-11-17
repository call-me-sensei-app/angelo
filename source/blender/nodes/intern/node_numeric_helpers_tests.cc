/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#include "testing/testing.h"

#include <cmath>

#include "node_composite_scale_render_size.hh"
#include "node_xpbd_friction.hh"

namespace blender::nodes::tests {

TEST(xpbd_contact_friction, PointAgainstCollider)
{
  EXPECT_FLOAT_EQ(xpbd_friction::contact_friction(0.25f, 1.0f), 0.5f);
  EXPECT_EQ(xpbd_friction::contact_friction(-1.0f, 1.0f), 0.0f);
  EXPECT_EQ(xpbd_friction::contact_friction(1.0f, -1.0f), 0.0f);
  EXPECT_EQ(xpbd_friction::contact_friction(-1.0f, -1.0f), 0.0f);
}

TEST(xpbd_contact_friction, EdgeAgainstColliderClampsEachOperand)
{
  EXPECT_NEAR(xpbd_friction::contact_friction(0.125f, 1.0f, 1.0f), 0.5f, 1e-6f);
  EXPECT_NEAR(xpbd_friction::contact_friction(0.5f, 0.5f, 0.5f), 0.5f, 1e-6f);
  /* One negative operand, wherever it is: no NaN, no friction. */
  EXPECT_EQ(xpbd_friction::contact_friction(-1.0f, 1.0f, 1.0f), 0.0f);
  EXPECT_EQ(xpbd_friction::contact_friction(1.0f, -1.0f, 1.0f), 0.0f);
  EXPECT_EQ(xpbd_friction::contact_friction(1.0f, 1.0f, -1.0f), 0.0f);
  /* Two negatives must not multiply into a positive coefficient. */
  EXPECT_EQ(xpbd_friction::contact_friction(-1.0f, -1.0f, 1.0f), 0.0f);
  EXPECT_EQ(xpbd_friction::contact_friction(-1.0f, 1.0f, -1.0f), 0.0f);
  EXPECT_EQ(xpbd_friction::contact_friction(-1.0f, -1.0f, -1.0f), 0.0f);
  EXPECT_EQ(xpbd_friction::contact_friction(0.0f, 1.0f, 1.0f), 0.0f);
}

static bool is_finite(const float2 &value)
{
  return std::isfinite(value.x) && std::isfinite(value.y);
}

TEST(scale_render_size, OrdinaryScalesAreUnchanged)
{
  const float2 size(100.0f, 50.0f), scale(1.0f, 1.0f), render(200.0f, 200.0f);
  EXPECT_EQ(scale_render_size::stretch(size, scale, render), float2(2.0f, 4.0f));
  EXPECT_EQ(scale_render_size::fit(size, scale, render), float2(2.0f));
  EXPECT_EQ(scale_render_size::crop(size, scale, render), float2(4.0f));
  EXPECT_EQ(scale_render_size::stretch(size, float2(2.0f, 0.5f), render), float2(1.0f, 8.0f));
}

TEST(scale_render_size, DegenerateInputScalesStayFinite)
{
  const float2 size(100.0f, 50.0f), render(200.0f, 200.0f);
  for (const float2 &scale : {float2(0.0f, 0.0f), float2(0.0f, 1.0f), float2(1.0f, 0.0f)}) {
    EXPECT_TRUE(is_finite(scale_render_size::stretch(size, scale, render)));
    EXPECT_TRUE(is_finite(scale_render_size::fit(size, scale, render)));
    EXPECT_TRUE(is_finite(scale_render_size::crop(size, scale, render)));
  }
  /* A zero axis gets no scale; the other axis keeps its own. */
  EXPECT_EQ(scale_render_size::stretch(size, float2(0.0f, 1.0f), render), float2(0.0f, 4.0f));
  EXPECT_EQ(scale_render_size::fit(size, float2(0.0f, 1.0f), render), float2(0.0f));
  EXPECT_EQ(scale_render_size::crop(size, float2(0.0f, 1.0f), render), float2(4.0f));
  EXPECT_EQ(scale_render_size::stretch(size, float2(0.0f), render), float2(0.0f));
  /* An empty input is the same degenerate case. */
  EXPECT_TRUE(is_finite(scale_render_size::stretch(float2(0.0f), float2(1.0f), render)));
}

}  // namespace blender::nodes::tests
