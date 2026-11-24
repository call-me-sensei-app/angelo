/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo
 */

#include "BKE_attribute.hh"
#include "BKE_mesh.hh"

#include "GEO_mesh_retopology.hh"

namespace blender::geometry {

/** Edges the person marked: Sharp, Seam and Crease (any crease above zero). */
static std::vector<std::array<int, 2>> marked_edges(const Mesh &mesh)
{
  const bke::AttributeAccessor attributes = mesh.attributes();
  const Span<int2> edges = mesh.edges();
  std::vector<std::array<int, 2>> marked;
  const VArraySpan<bool> sharp = *attributes.lookup_or_default<bool>(
      "sharp_edge", bke::AttrDomain::Edge, false);
  const VArraySpan<bool> seam = *attributes.lookup_or_default<bool>(
      "uv_seam", bke::AttrDomain::Edge, false);
  const VArraySpan<float> crease = *attributes.lookup_or_default<float>(
      "crease_edge", bke::AttrDomain::Edge, 0.0f);
  for (const int e : edges.index_range()) {
    if (sharp[e] || seam[e] || crease[e] > 0.0f) {
      marked.push_back({edges[e][0], edges[e][1]});
    }
  }
  return marked;
}

RetopologyResult mesh_retopology(const Mesh &mesh,
                                 const RetopologyOptions &options,
                                 const Span<Vector<float3>> guides,
                                 const FunctionRef<bool()> cancelled,
                                 const FunctionRef<void(float)> progress)
{
  retopo::TriMesh input;
  const Span<float3> positions = mesh.vert_positions();
  input.positions.resize(positions.size());
  for (const int v : positions.index_range()) {
    input.positions[v] = {positions[v].x, positions[v].y, positions[v].z};
  }
  const Span<int3> corner_tris = mesh.corner_tris();
  const Span<int> corner_verts = mesh.corner_verts();
  input.tris.resize(corner_tris.size());
  for (const int t : corner_tris.index_range()) {
    input.tris[t] = {corner_verts[corner_tris[t][0]],
                     corner_verts[corner_tris[t][1]],
                     corner_verts[corner_tris[t][2]]};
  }

  retopo::Params params;
  params.target_faces = std::max(options.target_faces, 1);
  params.symmetry_axes = options.symmetry_axes & 7;
  params.sharp_angle_degrees = double(options.sharp_angle) * 180.0 / 3.14159265358979323846;
  params.curvature_weight = options.curvature_weight;
  params.pure_quads = options.pure_quads;

  retopo::Constraints constraints;
  if (options.use_marked_edges) {
    constraints.marked_edges = marked_edges(mesh);
  }
  for (const Vector<float3> &guide : guides) {
    std::vector<retopo::Vec3> line;
    for (const float3 &p : guide) {
      line.push_back({p.x, p.y, p.z});
    }
    constraints.guides.push_back(std::move(line));
  }

  retopo::Callbacks callbacks;
  if (cancelled) {
    callbacks.cancelled = [&]() { return cancelled(); };
  }
  if (progress) {
    callbacks.progress = [&](const float f) { progress(f); };
  }

  retopo::Result core = retopo::remesh(input, params, constraints, callbacks);
  RetopologyResult result;
  result.status = core.status;
  result.facts = core.facts;
  result.intake = core.intake;
  result.times = core.times;
  result.field_solves = core.field_solves;
  result.target_edge_length = core.target_edge_length;
  for (std::string &note : core.notes) {
    result.notes.append(std::move(note));
  }
  if (core.status != retopo::Status::Ok) {
    return result;
  }

  const retopo::PolyMesh &out = core.mesh;
  Mesh *new_mesh = BKE_mesh_new_nomain(
      int(out.positions.size()), 0, out.faces_num(), int(out.corner_verts.size()));
  BKE_mesh_copy_parameters(new_mesh, &mesh);
  MutableSpan<float3> new_positions = new_mesh->vert_positions_for_write();
  for (const int v : new_positions.index_range()) {
    new_positions[v] = float3(
        float(out.positions[v].x), float(out.positions[v].y), float(out.positions[v].z));
  }
  new_mesh->face_offsets_for_write().copy_from(
      Span<int>(out.face_offsets.data(), out.face_offsets.size()));
  new_mesh->corner_verts_for_write().copy_from(
      Span<int>(out.corner_verts.data(), out.corner_verts.size()));
  bke::mesh_calc_edges(*new_mesh, false, false);
  result.mesh = new_mesh;
  return result;
}

}  // namespace blender::geometry
