/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup overlay
 *
 * Angelo: the viewport UV checker (overlay_uv_checker.hh, doc/angelo/uv_checker.rst).
 */

#ifdef GPU_SHADER
#  pragma once
#  include "gpu_shader_compat.hh"

#  include "draw_object_infos_infos.hh"
#  include "draw_view_infos.hh"
#endif

#include "overlay_common_infos.hh"

GPU_SHADER_INTERFACE_INFO(overlay_uv_checker_iface)
SMOOTH(float3, world_pos)
SMOOTH(float3, world_nor)
SMOOTH(float2, uv_interp)
GPU_SHADER_INTERFACE_END()

/* A mesh's surface with the UV map the checker reads aliased `cu<slot>`
 * (#DRW_mesh_batch_cache_get_surface_uv_checker): one variant per slot below. */
GPU_SHADER_CREATE_INFO(overlay_uv_checker)
VERTEX_IN(0, float3, pos)
VERTEX_IN(1, float3, nor)
VERTEX_OUT(overlay_uv_checker_iface)
SAMPLER(0, sampler2D, pattern_tx)
/* #eView3DUVCheckerPattern. */
PUSH_CONSTANT(int, pattern)
PUSH_CONSTANT(float, pattern_scale)
PUSH_CONSTANT(float, opacity)
/* Texture size / (unit scale * target density): texels per meter over the target, per UV unit. */
PUSH_CONSTANT(float, density_factor)
PUSH_CONSTANT(bool, show_flipped)
FRAGMENT_OUT(0, float4, frag_color)
VERTEX_SOURCE("overlay_uv_checker_vert.glsl")
FRAGMENT_SOURCE("overlay_uv_checker_frag.glsl")
ADDITIONAL_INFO(draw_view)
ADDITIONAL_INFO(draw_modelmat)
ADDITIONAL_INFO(draw_globals)
GPU_SHADER_CREATE_END()

GPU_SHADER_CREATE_INFO(overlay_uv_checker_slot0)
VERTEX_IN(2, float2, cu0)
GPU_SHADER_CREATE_END()
GPU_SHADER_CREATE_INFO(overlay_uv_checker_slot1)
VERTEX_IN(2, float2, cu1)
DEFINE("UV_CHECKER_SLOT_1")
GPU_SHADER_CREATE_END()
GPU_SHADER_CREATE_INFO(overlay_uv_checker_slot2)
VERTEX_IN(2, float2, cu2)
DEFINE("UV_CHECKER_SLOT_2")
GPU_SHADER_CREATE_END()
GPU_SHADER_CREATE_INFO(overlay_uv_checker_slot3)
VERTEX_IN(2, float2, cu3)
DEFINE("UV_CHECKER_SLOT_3")
GPU_SHADER_CREATE_END()

/* clang-format off */
CREATE_INFO_VARIANT(overlay_uv_checker_0, overlay_uv_checker, overlay_uv_checker_slot0)
CREATE_INFO_VARIANT(overlay_uv_checker_1, overlay_uv_checker, overlay_uv_checker_slot1)
CREATE_INFO_VARIANT(overlay_uv_checker_2, overlay_uv_checker, overlay_uv_checker_slot2)
CREATE_INFO_VARIANT(overlay_uv_checker_3, overlay_uv_checker, overlay_uv_checker_slot3)
CREATE_INFO_VARIANT(overlay_uv_checker_0_clipped, overlay_uv_checker_0, drw_clipped)
CREATE_INFO_VARIANT(overlay_uv_checker_1_clipped, overlay_uv_checker_1, drw_clipped)
CREATE_INFO_VARIANT(overlay_uv_checker_2_clipped, overlay_uv_checker_2, drw_clipped)
CREATE_INFO_VARIANT(overlay_uv_checker_3_clipped, overlay_uv_checker_3, drw_clipped)
/* clang-format on */

/* A mesh without the UV map: hatched, so a missing or misnamed map is obvious. */
GPU_SHADER_CREATE_INFO(overlay_uv_checker_missing)
DO_STATIC_COMPILATION()
VERTEX_IN(0, float3, pos)
PUSH_CONSTANT(float, opacity)
FRAGMENT_OUT(0, float4, frag_color)
VERTEX_SOURCE("overlay_uv_checker_missing_vert.glsl")
FRAGMENT_SOURCE("overlay_uv_checker_missing_frag.glsl")
ADDITIONAL_INFO(draw_view)
ADDITIONAL_INFO(draw_modelmat)
ADDITIONAL_INFO(draw_globals)
GPU_SHADER_CREATE_END()

CREATE_INFO_VARIANT(overlay_uv_checker_missing_clipped, overlay_uv_checker_missing, drw_clipped)
