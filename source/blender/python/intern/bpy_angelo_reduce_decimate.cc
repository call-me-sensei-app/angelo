/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 */

#include "BKE_attribute.hh"
#include "BKE_deform.hh"
#include "BKE_lib_id.hh"
#include "BKE_mesh.h"
#include "BKE_mesh.hh"

#include "DNA_mesh_types.h"
#include "DNA_meshdata_types.h"

#include "bmesh.hh"
#include "bmesh_tools.hh"

#include "bpy_angelo_reduce_decimate.hh"

namespace blender::angelo_reduce {

DecimateResult decimate(const Mesh &mesh,
                        const float ratio,
                        const bool triangulate,
                        const int symmetry_axis,
                        Span<int> groups)
{
  /* The conversion and call of `MOD_decimate.cc` for the Collapse mode, without vertex-group weighting. */
  BMeshCreateParams create_params{};
  BMeshFromMeshParams convert_params{};
  convert_params.calc_face_normal = true;
  convert_params.calc_vert_normal = true;
  BMesh *bm = BKE_mesh_to_bmesh_ex(&mesh, &create_params, &convert_params);
  BM_mesh_decimate_collapse(bm, ratio, nullptr, 0.0f, triangulate, symmetry_axis, 0.00002f);
  Mesh *result = BKE_mesh_from_bmesh_for_eval_nomain(bm, nullptr, &mesh);
  BM_mesh_free(bm);

  DecimateResult out;
  const Span<float3> positions = result->vert_positions();
  const Span<int> corner_verts = result->corner_verts();
  const Span<int3> corner_tris = result->corner_tris();
  const Span<float3> normals = result->corner_normals();
  out.positions = Array<float3>(positions);
  out.tris = Array<int3>(corner_tris.size());
  out.corner_normals = Array<float3>(corner_tris.size() * 3);
  for (const int i : corner_tris.index_range()) {
    const int3 &tri = corner_tris[i];
    out.tris[i] = int3(corner_verts[tri.x], corner_verts[tri.y], corner_verts[tri.z]);
    for (const int k : IndexRange(3)) {
      out.corner_normals[i * 3 + k] = normals[tri[k]];
    }
  }
  const StringRefNull uv_name = result->active_uv_map_name();
  if (!uv_name.is_empty()) {
    const bke::AttributeAccessor attributes = result->attributes();
    const VArraySpan uvs = *attributes.lookup<float2>(uv_name, bke::AttrDomain::Corner);
    if (!uvs.is_empty()) {
      out.uv_map = uv_name;
      out.corner_uvs = Array<float2>(corner_tris.size() * 3);
      for (const int i : corner_tris.index_range()) {
        for (const int k : IndexRange(3)) {
          out.corner_uvs[i * 3 + k] = uvs[corner_tris[i][k]];
        }
      }
    }
  }
  if (!groups.is_empty()) {
    out.weights = vertex_weights(*result, groups);
  }
  BKE_id_free(nullptr, result);
  return out;
}

Array<float> vertex_weights(const Mesh &mesh, Span<int> groups)
{
  const Span<MDeformVert> dverts = mesh.deform_verts();
  Array<float> weights(int64_t(mesh.verts_num) * groups.size(), 0.0f);
  if (dverts.is_empty()) {
    return weights;
  }
  for (const int v : dverts.index_range()) {
    for (const int g : groups.index_range()) {
      weights[v * groups.size() + g] = BKE_defvert_find_weight(&dverts[v], groups[g]);
    }
  }
  return weights;
}

}  // namespace blender::angelo_reduce
