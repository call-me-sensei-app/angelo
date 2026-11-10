/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "DNA_view3d_types.h"

#include "DRW_engine.hh"
#include "ED_view3d.hh"

#include "workbench_private.hh"

namespace blender::workbench {

ShaderCache::ShaderCache()
{
  const std::string geometries[] = {"_mesh", "_curves", "_ptcloud"};
  const std::string pipelines[] = {"_opaque", "_transparent"};
  const std::string lightings[] = {"_flat", "_studio", "_matcap"};
  const std::string shaders[] = {"_material", "_texture"};
  const std::string clip[] = {"_no_clip", "_clip"};
  static_assert(std::size(geometries) == geometry_type_len);
  static_assert(std::size(pipelines) == pipeline_type_len);
  static_assert(std::size(lightings) == lighting_type_len);
  static_assert(std::size(shaders) == shader_type_len);

  for (auto g : IndexRange(geometry_type_len)) {
    for (auto p : IndexRange(pipeline_type_len)) {
      for (auto l : IndexRange(lighting_type_len)) {
        for (auto s : IndexRange(shader_type_len)) {
          for (auto c : IndexRange(2) /*clip*/) {
            prepass_[g][p][l][s][c] = {"workbench_prepass" + geometries[g] + pipelines[p] +
                                       lightings[l] + shaders[s] + clip[c]};
          }
        }
      }
    }
  }

  const std::string cavity[] = {"_no_cavity", "_cavity"};
  const std::string curvature[] = {"_no_curvature", "_curvature"};
  const std::string shadow[] = {"_no_shadow", "_shadow"};

  for (auto l : IndexRange(lighting_type_len)) {
    for (auto ca : IndexRange(2) /*cavity*/) {
      for (auto cu : IndexRange(2) /*curvature*/) {
        for (auto s : IndexRange(2) /*shadow*/) {
          resolve_[l][ca][cu][s] = {"workbench_resolve_opaque" + lightings[l] + cavity[ca] +
                                    curvature[cu] + shadow[s]};
        }
      }
    }
  }

  const std::string pass[] = {"_fail", "_pass"};
  const std::string manifold[] = {"_no_manifold", "_manifold"};
  const std::string caps[] = {"_no_caps", "_caps"};

  for (auto p : IndexRange(2) /*pass*/) {
    for (auto m : IndexRange(2) /*manifold*/) {
      for (auto c : IndexRange(2) /*caps*/) {
        shadow_[p][m][c] = {"workbench_shadow" + pass[p] + manifold[m] + caps[c] +
                            (DEBUG_SHADOW_VOLUME ? "_debug" : "")};
      }
    }
  }

  const std::string smoke[] = {"_object", "_smoke"};
  const std::string interpolation[] = {"_linear", "_cubic", "_closest"};
  const std::string coba[] = {"_no_coba", "_coba"};
  const std::string slice[] = {"_no_slice", "_slice"};

  for (auto sm : IndexRange(2) /*smoke*/) {
    for (auto i : IndexRange(3) /*interpolation*/) {
      for (auto c : IndexRange(2) /*coba*/) {
        for (auto sl : IndexRange(2) /*slice*/) {
          volume_[sm][i][c][sl] = {"workbench_volume" + smoke[sm] + interpolation[i] + coba[c] +
                                   slice[sl]};
        }
      }
    }
  }
}

void ShaderCache::compile_async(const View3D &v3d, const RegionView3D *rv3d)
{
  /* The settings #SceneState::init derives from a Solid view (workbench_state.cc). */
  View3DShading shading = v3d.shading;
  const bool xray = SHADING_XRAY_ENABLED(shading);
  if (xray) {
    shading.flag &= ~(V3D_SHADING_SHADOW | V3D_SHADING_CAVITY | V3D_SHADING_DEPTH_OF_FIELD);
  }
  const int lighting = int(lighting_type_from_v3d_lighting(shading.light));
  const bool clip = RV3D_CLIPPING_ENABLED(&v3d, rv3d);
  const bool cavity = shading.flag & V3D_SHADING_CAVITY &&
                      ELEM(shading.cavity_type, V3D_SHADING_CAVITY_SSAO, V3D_SHADING_CAVITY_BOTH);
  const bool curvature = shading.flag & V3D_SHADING_CAVITY &&
                         ELEM(shading.cavity_type,
                              V3D_SHADING_CAVITY_CURVATURE,
                              V3D_SHADING_CAVITY_BOTH);
  const bool shadow = shading.flag & V3D_SHADING_SHADOW;

  /* Meshes, the pipeline X-ray selects; textures only when the view colours by texture. */
  const int pipeline = int(xray ? ePipelineType::TRANSPARENT : ePipelineType::OPAQUE);
  const int mesh = int(eGeometryType::MESH);
  prepass_[mesh][pipeline][lighting][int(eShaderType::MATERIAL)][clip].ensure_compile_async();
  if (shading.color_type == V3D_SHADING_TEXTURE_COLOR) {
    prepass_[mesh][pipeline][lighting][int(eShaderType::TEXTURE)][clip].ensure_compile_async();
  }
  resolve_[lighting][cavity][curvature][shadow].ensure_compile_async();

  /* Requested by every Solid draw's sync (transparency, depth merge, anti-aliasing). */
  transparent_resolve.ensure_compile_async();
  merge_depth.ensure_compile_async();
  taa_accumulation.ensure_compile_async();
  smaa_edge_detect.ensure_compile_async();
  smaa_aa_weight.ensure_compile_async();
  smaa_resolve.ensure_compile_async();
  overlay_depth.ensure_compile_async();
  if (shading.flag & V3D_SHADING_OBJECT_OUTLINE) {
    outline.ensure_compile_async();
  }
  if (shadow) {
    shadow_visibility_dynamic.ensure_compile_async();
    shadow_visibility_static.ensure_compile_async();
  }
}

}  // namespace blender::workbench

namespace blender {

void DRW_workbench_shaders_compile_async(const View3D *v3d, const RegionView3D *rv3d)
{
  if (v3d == nullptr || v3d->shading.type != OB_SOLID) {
    return; /* Workbench draws Solid views; Material and Rendered views are EEVEE's or Cycles'. */
  }
  workbench::ShaderCache::get().compile_async(*v3d, rv3d);
}

}  // namespace blender
