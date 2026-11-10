/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup overlay
 */

#include "DRW_engine.hh"

#include "overlay_private.hh"

namespace blender::draw::overlay {

StaticShader ShaderModule::shader_clippable(const char *create_info_name)
{
  std::string name = create_info_name;

  if (clipping_enabled_) {
    name += "_clipped";
  }

  return StaticShader(name);
}

StaticShader ShaderModule::shader_selectable(const char *create_info_name)
{
  std::string name = create_info_name;

  if (selection_type_ != SelectionType::DISABLED) {
    name += "_selectable";
  }

  if (clipping_enabled_) {
    name += "_clipped";
  }

  return StaticShader(name);
}

StaticShader ShaderModule::shader_selectable_no_clip(const char *create_info_name)
{
  std::string name = create_info_name;

  if (selection_type_ != SelectionType::DISABLED) {
    name += "_selectable";
  }

  return StaticShader(name);
}

using namespace blender::gpu::shader;

void ShaderModule::compile_async()
{
  anti_aliasing.ensure_compile_async();
  armature_degrees_of_freedom.ensure_compile_async();
  armature_envelope_fill.ensure_compile_async();
  armature_envelope_outline.ensure_compile_async();
  armature_shape_fill.ensure_compile_async();
  armature_shape_outline.ensure_compile_async();
  armature_shape_wire_strip.ensure_compile_async();
  armature_shape_wire.ensure_compile_async();
  armature_sphere_fill.ensure_compile_async();
  armature_sphere_outline.ensure_compile_async();
  armature_stick.ensure_compile_async();
  armature_wire.ensure_compile_async();
  attribute_viewer_curve.ensure_compile_async();
  attribute_viewer_curves.ensure_compile_async();
  attribute_viewer_mesh.ensure_compile_async();
  attribute_viewer_pointcloud.ensure_compile_async();
  tension_mesh.ensure_compile_async();
  background_fill.ensure_compile_async();
  curve_edit_handles.ensure_compile_async();
  curve_edit_line.ensure_compile_async();
  curve_edit_points.ensure_compile_async();
  depth_curves.ensure_compile_async();
  depth_grease_pencil.ensure_compile_async();
  depth_mesh.ensure_compile_async();
  depth_pointcloud.ensure_compile_async();
  extra_grid.ensure_compile_async();
  extra_ground_line.ensure_compile_async();
  extra_loose_points.ensure_compile_async();
  extra_point.ensure_compile_async();
  extra_shape.ensure_compile_async();
  extra_wire_object.ensure_compile_async();
  extra_wire.ensure_compile_async();
  fluid_grid_lines_flags.ensure_compile_async();
  fluid_grid_lines_flat.ensure_compile_async();
  fluid_grid_lines_range.ensure_compile_async();
  fluid_velocity_mac.ensure_compile_async();
  fluid_velocity_needle.ensure_compile_async();
  fluid_velocity_streamline.ensure_compile_async();
  grid.ensure_compile_async();
  image_plane_depth_bias.ensure_compile_async();
  lattice_points.ensure_compile_async();
  lattice_wire.ensure_compile_async();
  legacy_curve_edit_handles.ensure_compile_async();
  legacy_curve_edit_points.ensure_compile_async();
  legacy_curve_edit_wires.ensure_compile_async();
  light_spot_cone.ensure_compile_async();
  mesh_analysis.ensure_compile_async();
  mesh_edit_depth.ensure_compile_async();
  mesh_edit_edge.ensure_compile_async();
  mesh_edit_face.ensure_compile_async();
  mesh_edit_facedot.ensure_compile_async();
  mesh_edit_skin_root.ensure_compile_async();
  mesh_edit_vert.ensure_compile_async();
  motion_path_line.ensure_compile_async();
  motion_path_vert.ensure_compile_async();
  outline_detect.ensure_compile_async();
  outline_prepass_curves.ensure_compile_async();
  outline_prepass_gpencil.ensure_compile_async();
  outline_prepass_mesh.ensure_compile_async();
  outline_prepass_pointcloud.ensure_compile_async();
  outline_prepass_wire.ensure_compile_async();
  paint_weight_fake_shading.ensure_compile_async();
  particle_dot.ensure_compile_async();
  particle_edit_edge.ensure_compile_async();
  particle_edit_vert.ensure_compile_async();
  particle_hair.ensure_compile_async();
  particle_shape.ensure_compile_async();
  pointcloud_points.ensure_compile_async();
  uniform_color.ensure_compile_async();
  wireframe_curve.ensure_compile_async();
  wireframe_mesh.ensure_compile_async();
  wireframe_points.ensure_compile_async();
}

ShaderModule &ShaderModule::module_get(SelectionType selection_type, bool clipping_enabled)
{
  int selection_index = selection_type == SelectionType::DISABLED ? 0 : 1;
  return get_static_cache()[selection_index][clipping_enabled].get(selection_type,
                                                                   clipping_enabled);
}

void ShaderModule::module_free()
{
  for (int i : IndexRange(2)) {
    for (int j : IndexRange(2)) {
      get_static_cache()[i][j].release();
    }
  }
}

}  // namespace blender::draw::overlay

namespace blender {

void DRW_overlay_shaders_compile_async()
{
  /* The module of the default viewport: no selection, no clipping. */
  draw::overlay::ShaderModule::module_get(draw::select::SelectionType::DISABLED, false)
      .compile_async();
}

}  // namespace blender
