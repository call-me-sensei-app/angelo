/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "infos/overlay_uv_checker_infos.hh"

VERTEX_SHADER_CREATE_INFO(overlay_uv_checker_0)

#include "draw_model_lib.glsl"
#include "draw_view_clipping_lib.glsl"
#include "draw_view_lib.glsl"

void main()
{
  world_pos = drw_point_object_to_world(pos);
  world_nor = drw_normal_object_to_world(nor);
#if defined(UV_CHECKER_SLOT_1)
  uv_interp = cu1;
#elif defined(UV_CHECKER_SLOT_2)
  uv_interp = cu2;
#elif defined(UV_CHECKER_SLOT_3)
  uv_interp = cu3;
#else
  uv_interp = cu0;
#endif
  gl_Position = drw_point_world_to_homogenous(world_pos);
  view_clipping_distances(world_pos);
}
