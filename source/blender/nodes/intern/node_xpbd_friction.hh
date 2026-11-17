/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup nodes
 *
 * Contact friction coefficients of the XPBD solver: the geometric mean of the friction of the
 * contact's points and of the collider. Friction values stored in attributes may be negative.
 */

#include <algorithm>

#include "BLI_math_base.hh"

namespace blender::nodes::xpbd_friction {

/** A point against a collider. */
inline float contact_friction(const float point_friction, const float collider_friction)
{
  /* Friction values stored in attributes may be negative which would lead to NaN. */
  return math::sqrt(std::max(point_friction, 0.0f) * std::max(collider_friction, 0.0f));
}

/** An edge (its two points) against a collider. */
inline float contact_friction(const float point0_friction,
                              const float point1_friction,
                              const float collider_friction)
{
  /* Clamp each operand: one negative value would give NaN, two a positive coefficient. */
  return math::pow(std::max(point0_friction, 0.0f) * std::max(point1_friction, 0.0f) *
                       std::max(collider_friction, 0.0f),
                   1.0f / 3.0f);
}

}  // namespace blender::nodes::xpbd_friction
