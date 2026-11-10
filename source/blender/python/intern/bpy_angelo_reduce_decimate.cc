/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 */

#include <algorithm>

#include "BLI_map.hh"

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

Mesh *snapshot(const Mesh &mesh)
{
  return BKE_mesh_copy_for_eval(mesh);
}

void snapshot_free(Mesh *mesh)
{
  BKE_id_free(nullptr, mesh);
}

#ifdef WITH_BULLET
bool convex_hull(const Span<float3> points, Vector<float3> &r_positions, Vector<int3> &r_tris)
{
  BMeshCreateParams params{};
  /* Operators keep their flags there (`bmesh.new()` makes the same). */
  params.use_toolflags = true;
  BMesh *bm = BM_mesh_create(&bm_mesh_allocsize_default, &params);
  for (const float3 &point : points) {
    BM_vert_create(bm, point, nullptr, BM_CREATE_NOP);
  }
  BMOperator op;
  BMO_op_initf(bm, &op, BMO_FLAG_DEFAULTS, "convex_hull input=%av use_existing_faces=%b", false);
  BMO_op_exec(bm, &op);
  Map<const BMVert *, int> index;
  BMOIter iter;
  BMFace *face;
  BMO_ITER (face, &iter, op.slots_out, "geom.out", BM_FACE) {
    Vector<int, 3> corners;
    BMIter loops;
    BMLoop *loop;
    BM_ITER_ELEM (loop, &loops, face, BM_LOOPS_OF_FACE) {
      corners.append(index.lookup_or_add_cb(loop->v, [&]() {
        r_positions.append(float3(loop->v->co));
        return int(r_positions.size() - 1);
      }));
    }
    /* The hull's faces are triangles; fan any that are not. */
    for (const int k : IndexRange(1, std::max<int>(corners.size() - 2, 0))) {
      r_tris.append(int3(corners[0], corners[k], corners[k + 1]));
    }
  }
  BMO_op_finish(bm, &op);
  BM_mesh_free(bm);
  if (r_tris.is_empty()) {
    r_positions.clear();
    return false;
  }
  return true;
}
#endif

}  // namespace blender::angelo_reduce
