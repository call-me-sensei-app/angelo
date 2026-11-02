/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* Angelo: tension map overlay. */

#ifdef GPU_SHADER
#  pragma once
#  include "gpu_shader_compat.hh"

#  include "draw_object_infos_infos.hh"
#  include "draw_view_infos.hh"
#  include "overlay_common_infos.hh"
#endif

#include "overlay_common_infos.hh"

GPU_SHADER_INTERFACE_INFO(overlay_tension_iface)
SMOOTH(float, tension_value)
GPU_SHADER_INTERFACE_END()

GPU_SHADER_CREATE_INFO(overlay_tension_mesh)
DO_STATIC_COMPILATION()
VERTEX_SOURCE("overlay_tension_vert.glsl")
FRAGMENT_SOURCE("overlay_tension_frag.glsl")
FRAGMENT_OUT(0, float4, out_color)
FRAGMENT_OUT(1, float4, line_output)
VERTEX_IN(0, float3, pos)
VERTEX_IN(1, float, tension)
VERTEX_OUT(overlay_tension_iface)
PUSH_CONSTANT(float, compress_max)
PUSH_CONSTANT(float, stretch_max)
PUSH_CONSTANT(float, opacity)
ADDITIONAL_INFO(draw_view)
ADDITIONAL_INFO(draw_modelmat)
ADDITIONAL_INFO(draw_globals)
GPU_SHADER_CREATE_END()

CREATE_INFO_VARIANT(overlay_tension_mesh_clipped, overlay_tension_mesh, drw_clipped)
