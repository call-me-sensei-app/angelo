/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup nodes
 *
 * The Scale node's Render Size modes: the scale that makes an input of `input_size` pixels,
 * already scaled by `input_scale`, match `render_size`.
 */

#include "BLI_math_vector.hh"
#include "BLI_math_vector_types.hh"

namespace blender::nodes::scale_render_size {

/**
 * The scale whose result matches the render size on both axes (the input may be stretched).
 * A degenerate input (zero size or zero scale on an axis) gets no scale on that axis: dividing
 * would give an infinite scale, and the zero-scale input times it a non-finite transform.
 */
inline float2 stretch(const float2 &input_size, const float2 &input_scale, const float2 &render_size)
{
  return math::safe_divide(render_size, input_size * input_scale);
}

/** The smaller axis scale on both axes: the input fits inside the render size. */
inline float2 fit(const float2 &input_size, const float2 &input_scale, const float2 &render_size)
{
  const float2 scale = stretch(input_size, input_scale, render_size);
  return float2(math::min(scale.x, scale.y));
}

/** The larger axis scale on both axes: the input covers the render size and is cropped. */
inline float2 crop(const float2 &input_size, const float2 &input_scale, const float2 &render_size)
{
  const float2 scale = stretch(input_size, input_scale, render_size);
  return float2(math::max(scale.x, scale.y));
}

}  // namespace blender::nodes::scale_render_size
